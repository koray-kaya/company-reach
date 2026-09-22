"""Find a company's website, and prove it belongs to that company.

Three ways to believe a site is the right one, strongest first:

* **uid** — the register's UID is on the page, check digit and all. That
  cannot be a coincidence, so it is the strongest label a site can carry.
* **address** — the register's street and postal code are on the page.
  Corroboration, not proof: hundreds of firms share a town.
* **model** — the model read the candidates and chose, returning a quote
  that this code then looks for in the page it was taken from. The quote is
  the point: it proves grounding, not identity, which is exactly why it is
  the weakest tier rather than the strongest.

None of these outranks the model's own "none of these", and the first real
run is why. A company directory publishes the UIDs of the firms it lists, so
its page matched tier 1; the model saw a directory and rejected it; the
rejection was overruled and a directory was recorded as the company's
website, with the highest confidence label we have. A UID on a page proves
the page is *about* the company. It never proved the page *belongs to* it —
which is exactly the distinction tier 3 was already built around.

So the model is the gate and the tiers are the label. It is asked for every
company, which also keeps the golden evaluation honest: a ladder stopping at
tier 1 would call the model twice in a real run and fifteen times in the
evaluation, and the evaluation would be measuring something we never ship.

Finally, the distinction the milestone turns on. No candidate found is a
**finding** — five of twenty companies in the earlier prototype had no
website, and that is a fact about the population. Search being broken is an
**error**, and it is raised, because "we could not look" must never be
written down as "this company has no website". Search answering nothing at
all, to every query and again after a pause, counts as broken: such a
company still has its directory entries, so silence means we were not heard.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from company_reach.errors import SearchError
from company_reach.models import CompanyRecord
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.blocklist import is_blocked
from company_reach.tools.candidate_pages import CandidatePages, read_candidate
from company_reach.tools.fetcher import Fetcher, resolve_host
from company_reach.tools.search import Result, search
from company_reach.tools.textify import normalise
from company_reach.tools.uid import uid_match

Tier = Literal["uid", "address", "model"]

_LEGAL_FORMS = ("AG", "GmbH", "SA", "Sàrl", "Sarl", "SAGL", "SagL")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
# The earlier prototype sent every plausible candidate, about ten, to one
# model call. A cap of three was tried here and lost the right site behind
# directories whenever search ranked them first; the model compares
# candidates side by side, so more of them costs tokens, not accuracy.
_MAX_CANDIDATES = 10
# Paths a sitemap has thousands of and a company profile needs none of.
_BULK = re.compile(
    r"/(produkt|product|shop|blog|news|artikel|tag|category|kategorie)(/|$)"
    r"|/20\d\d/",
    re.IGNORECASE,
)


class SiteAnswer(BaseModel):
    """What `pick_site.md` returns. No confidence field: a model's own
    confidence is poorly calibrated, and the tier is a better signal."""

    chosen_url: str | None = Field(description="null when none of them fits")
    quote: str | None = Field(description="copied exactly from the chosen page")
    reason: str


@dataclass(frozen=True)
class SiteChoice:
    url: str
    tier: Tier
    evidence: str
    evidence_url: str
    evidence_note: str = ""


# --- queries -----------------------------------------------------------------


def strip_legal_form(name: str) -> str:
    """`Muster Metallbau AG in Liquidation` → `Muster Metallbau`. The legal
    form is in every register name and on almost no website's title."""
    cleaned = re.sub(r"\bin Liquidation\b", "", name, flags=re.IGNORECASE)
    words = [w for w in cleaned.split() if w.strip(".,") not in _LEGAL_FORMS]
    return " ".join(words).strip(" ,.-")


def build_queries(record: CompanyRecord) -> list[str]:
    """Three queries, in the order the research settled: the quoted name with
    the seat, the name with `Impressum` (which lands on the page we need to
    read next), and the UID in both spellings."""
    name = strip_legal_form(record.name)
    seat = record.city or record.municipality
    dotted = f"CHE-{record.uid[3:6]}.{record.uid[6:9]}.{record.uid[9:]}"
    return [
        f'"{name}" {seat}',
        f"{name} Impressum",
        f'"{dotted}" OR "{record.uid}"',
    ]


def guess_domains(name: str) -> list[str]:
    """Four names from the company's own: joined and hyphenated, `.ch` and
    `.com`. Umlauts go the German way — `müller.ch`, `muller.ch` and
    `mueller.ch` are three different hosts and the third is the one Swiss
    firms register."""
    words = [
        re.sub(r"[^a-z0-9]", "", word.lower().translate(_UMLAUTS))
        for word in strip_legal_form(name).split()
    ]
    words = [w for w in words if w]
    if not words:
        return []
    joined, hyphenated = "".join(words), "-".join(words)
    stems = [joined] if joined == hyphenated else [joined, hyphenated]
    return [f"{stem}.{tld}" for stem in stems for tld in ("ch", "com")]


async def resolving_domains(names: list[str]) -> list[str]:
    """Only the guesses that exist. DNS costs nothing next to a fetch, and a
    guessed domain is a candidate like any other — it still has to pass the
    same three tiers, so a parked domain fails on its own."""

    async def resolves(host: str) -> str | None:
        try:
            return host if await resolve_host(host) else None
        except OSError:
            return None

    found = await asyncio.gather(*[resolves(name) for name in names])
    return [f"https://{host}/" for host in found if host]


# --- candidates --------------------------------------------------------------


def _registered_domain(url: str) -> str | None:
    host = urlsplit(url).hostname
    return host.lower().removeprefix("www.") if host else None


def site_root(url: str) -> str:
    """`https://muster.ch/home/impressum/` → `https://muster.ch/`.

    Search often finds a company through one of its inner pages. The
    candidate is the site, not the page: the home page and the Impressum
    are what get read, and both hang off the root."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "/", "", ""))


def dedupe_candidates(results: list[Result]) -> list[str]:
    """One root URL per registered domain, blocklisted domains dropped,
    order preserved. Three pages of the same site are one candidate."""
    seen: set[str] = set()
    kept: list[str] = []
    for result in results:
        if urlsplit(result.url).scheme not in ("http", "https"):
            continue
        if is_blocked(result.url):
            continue
        domain = _registered_domain(result.url)
        if domain is None or domain in seen:
            continue
        seen.add(domain)
        kept.append(site_root(result.url))
    return kept


# --- page listing ------------------------------------------------------------


def _normalise_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def prune_page_urls(urls: list[str], *, limit: int) -> list[str]:
    """Down to `limit`, dropping what a company profile never needs first.

    Sitemaps in the probe listed 1,659 and 4,599 URLs. Bulk paths go first —
    products, blog posts, dated archives — and what remains is sorted by
    depth, because Impressum, Kontakt and Über uns sit near the root while
    deep paths are individual items.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        cleaned = _normalise_url(url)
        if cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)

    plain = [u for u in unique if not _BULK.search(urlsplit(u).path)]
    ordered = sorted(plain, key=lambda u: (urlsplit(u).path.count("/"), len(u)))
    if len(ordered) >= limit:
        return ordered[:limit]
    bulk = [u for u in unique if u not in set(ordered)]
    return (ordered + bulk)[:limit]


def harvest_links(html: str, base: str) -> list[str]:
    """Same-host links from one page. One level deep is enough to reach
    Impressum, Kontakt and Über uns, which is what the next milestone reads;
    crawling further would be building for a need nobody has measured."""
    host = urlsplit(base).hostname
    found: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        absolute = urljoin(base, href)
        if urlsplit(absolute).scheme in ("http", "https") and (
            urlsplit(absolute).hostname == host
        ):
            found.append(absolute)
    return found


def read_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """(page urls, nested sitemap urls). Hand-rolled because the maintained
    parser is GPL and trafilatura's own helper hides the fetch, which would
    lose the cache, the user agent and the per-host delay."""
    locations = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, flags=re.DOTALL)
    if "<sitemapindex" in xml:
        return [], locations
    return locations, []


# --- verification ------------------------------------------------------------


def _address_on_page(record: CompanyRecord, text: str) -> str | None:
    """The register's postal code and town on one line, with the street
    somewhere in the text. The research validated the postal-code line on 13
    of 15 sites; the street is the part that makes it more than a town."""
    if not (record.postal_code and record.city):
        return None
    folded = normalise(text)
    line = normalise(f"{record.postal_code} {record.city}")
    if line not in folded:
        return None
    if record.street and normalise(record.street) not in folded:
        return None
    return f"{record.street}, {record.postal_code} {record.city}".strip(", ")


def verify(
    record: CompanyRecord, text: str
) -> tuple[Literal["uid", "address"] | None, str]:
    """The deterministic tiers. Returns the tier and its evidence."""
    match = uid_match(record.uid, text)
    if match == "match":
        return "uid", record.uid
    address = _address_on_page(record, text)
    if address is not None:
        return "address", address
    return None, ""


def quote_found(quote: str, text: str) -> bool:
    """Every piece of the quote is on the page.

    The model often quotes the name, skips a few lines and quotes the UID,
    joining the two with "...". Each piece is still copied from the page, so
    the choice is still grounded — what must not pass is a piece that is not
    there, and that is what is checked."""
    page = normalise(text)
    pieces = [normalise(p) for p in re.split(r"\.\.\.|…", quote)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(piece in page for piece in pieces)


# --- the node ----------------------------------------------------------------


def _candidate_block(url: str, pages: CandidatePages) -> str:
    """Page text is data, and it is delimited so it cannot be read as
    instructions — the same convention the scoring prompt uses."""
    return f"<<<PAGE url={url}>>>\n{pages.for_prompt()}\n<<<END>>>"


def narrowing_query(record: CompanyRecord) -> str:
    """The last resort: the same name, restricted to Swiss domains.

    The research is explicit that this must not *start* the search — leading
    with `site:.ch` throws away a company whose site is a `.com`. As a fourth
    query, after the first three brought back nothing but directories, it
    costs one search and sometimes finds the site those directories were
    describing."""
    name = strip_legal_form(record.name)
    seat = record.city or record.municipality
    return f'site:.ch "{name}" {seat}'


async def search_results(record: CompanyRecord, *, settings: Settings) -> list[Result]:
    """Every result, before dedupe and the cap. Kept apart from the choice of
    candidates so the golden-set capture can save what search returned —
    directories included, which is what the candidates exist to leave out.

    Domain guesses come first. A guess that resolves is built from the
    company's own name, which no search result can say; put last, it lost
    its place to whatever search ranked above it."""
    guessed = await resolving_domains(guess_domains(record.name))
    searched = await _ask_every_query(record, settings=settings)

    # Silence from every query is not an answer. A real company almost always
    # has at least a directory entry, and a throttled engine can return
    # nothing without listing itself as unresponsive (#17). Ask once more
    # after a pause; still nothing, and it is an error a later run retries —
    # never "no website". A resolving guess does not change that: it is
    # built from the name and says nothing about whether search was heard.
    if not searched:
        await asyncio.sleep(settings.search_retry_pause_s)
        searched = await _ask_every_query(record, settings=settings)
    if not searched:
        raise SearchError(
            "every query returned nothing, twice; search was probably "
            "throttled, and the company is not recorded as having no website"
        )

    # Search came back, and every single result was a directory or a social
    # profile. Narrowing to .ch is the one cheap thing left.
    if not dedupe_candidates(searched):
        searched.extend(await search(narrowing_query(record), settings=settings))

    return [Result(url, "", "", "guess") for url in guessed] + searched


async def _ask_every_query(
    record: CompanyRecord, *, settings: Settings
) -> list[Result]:
    results: list[Result] = []
    for query in build_queries(record):
        results.extend(await search(query, settings=settings))
    return results


def choose_candidates(results: list[Result]) -> list[str]:
    return dedupe_candidates(results)[:_MAX_CANDIDATES]


async def read_candidates(
    candidates: list[str], *, fetcher: Fetcher
) -> dict[str, CandidatePages]:
    """What each candidate says about itself. A candidate with no text at all
    is left out: the model cannot choose what it cannot read.

    Candidates are read at the same time, the way the earlier prototype
    did. They are different sites, so this does not touch the per-host
    delay; one site's pages are still read one after the other."""
    read = await asyncio.gather(
        *[read_candidate(url, fetcher=fetcher) for url in candidates]
    )
    return {
        url: pages
        for url, pages in zip(candidates, read, strict=True)
        if pages is not None and pages.full_text().strip()
    }


async def find_site(
    state: dict[str, Any], *, settings: Settings, fetcher: Fetcher | None = None
) -> dict:
    """`fetcher` is passed in so that every node of one child graph shares
    it. Sharing is not a convenience: the per-host delay and the robots cache
    are per-instance, so a fetcher per node would forget the delay between
    find_site's pages and the next node's, and re-read robots.txt each time."""
    record: CompanyRecord = state["company"]
    candidates = choose_candidates(await search_results(record, settings=settings))

    fetcher = fetcher or Fetcher(settings)
    pages = await read_candidates(candidates, fetcher=fetcher)
    if not pages:
        return _no_site(record, candidates)

    texts = {url: read.full_text() for url, read in pages.items()}
    answer = await _ask_model(record, pages, settings=settings)
    decided = _decide(record, texts, answer, candidates)
    if decided["site"] is not None:
        decided["page_urls"] = await list_pages(
            decided["site"].url, fetcher=fetcher, limit=settings.max_page_urls
        )
    return decided


async def list_pages(site: str, *, fetcher: Fetcher, limit: int) -> list[str]:
    """Every page worth considering, capped. The sitemap when there is one,
    the home page's own links when there is not.

    M5 chooses which of these to read; this only has to make sure the right
    ones are in the list at all."""
    found = await all_page_urls(site, fetcher=fetcher, limit=limit)
    return prune_page_urls(found, limit=limit)


async def all_page_urls(site: str, *, fetcher: Fetcher, limit: int) -> list[str]:
    """The list before pruning — what the prune patterns are judged against."""
    found: list[str] = []
    pending = [urljoin(site, "/sitemap.xml")]
    while pending and len(found) < limit * 10:
        page = await fetcher.get(pending.pop(0))
        if page.error is not None or not page.html:
            continue
        pages, nested = read_sitemap(page.html)
        found.extend(pages)
        pending.extend(nested[:20])

    if not found:
        home = await fetcher.get(site)
        if home.html:
            found = harvest_links(home.html, site)

    found.append(site)
    return found


async def _ask_model(
    record: CompanyRecord, pages: dict[str, CandidatePages], *, settings: Settings
) -> SiteAnswer:
    blocks = "\n\n".join(_candidate_block(url, read) for url, read in pages.items())
    address = ", ".join(
        part for part in (record.street, record.postal_code, record.city) if part
    )
    answer, _ = await llm.ask(
        "pick_site",
        SiteAnswer,
        settings=settings,
        name=record.name,
        address=address or "not recorded",
        seat=record.city or record.municipality,
        uid=record.uid,
        candidates=blocks,
    )
    return answer


def _decide(
    record: CompanyRecord,
    texts: dict[str, str],
    answer: SiteAnswer,
    candidates: list[str],
) -> dict:
    """The model is the gate; the tiers are the label.

    An earlier version let a UID match stand against a "none of these", on
    the reasoning that a check-digit-verified UID cannot be a coincidence.
    It cannot — but that proves the page is *about* the company, not that it
    *belongs to* the company. The first real run found the difference: a
    company directory publishes UIDs, so a directory page matched tier 1,
    the model correctly rejected it, and the rejection was overruled. It is
    the same grounding-is-not-identity trap tier 3 was already built around.

    So a rejection stands. A UID still outranks everything as *evidence* —
    it decides which label the site carries — it just no longer decides
    whether there is a site at all.
    """
    if answer.chosen_url is None or answer.chosen_url not in texts:
        return _no_site(record, candidates)

    chosen = answer.chosen_url
    quote = answer.quote or ""
    if not quote_found(quote, texts[chosen]):
        return _no_site(record, candidates)

    tier, evidence = verify(record, texts[chosen])
    if tier is None:
        tier, evidence = "model", quote

    # Worth recording: the register's UID sat on a page the model passed over.
    elsewhere = [
        url
        for url, text in texts.items()
        if url != chosen and verify(record, text)[0] == "uid"
    ]
    note = f"the register UID is also on {elsewhere[0]}" if elsewhere else ""

    return {
        "site": SiteChoice(chosen, tier, evidence, chosen, note),
        "recommendation": None,
        "reason": None,
    }


def _no_site(record: CompanyRecord, candidates: list[str]) -> dict:
    """The verdict is written here, where the finding is produced. The
    design put it in `recommend`, but the child ends before `recommend` when
    there is no site, so nothing would have written it at all."""
    tried = len(build_queries(record))
    looked = f", {len(candidates)} candidate(s) checked" if candidates else ""
    return {
        "site": None,
        "recommendation": "skip",
        "reason": f"no website found after {tried} searches and a domain guess{looked}",
    }
