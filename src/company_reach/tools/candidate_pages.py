"""What one candidate site says about itself: home page, Impressum, About
page and schema.org block — the four things the earlier prototype read, and
read well.

The Impressum is found the way a person finds it: by the link at the foot of
the home page. Guessing `/impressum` misses every site that puts it at
`/de/impressum` or `/impressum.html`, and the Impressum is where the UID and
the address are — the two things that prove a site belongs to a company. The
fixed paths are only the fallback for a home page with no such link.

The schema.org block costs no request: it sits in the home page's HTML, put
there for search engines, and often carries the legal name and address in a
form no layout can scramble.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from company_reach.tools.fetcher import Fetcher
from company_reach.tools.textify import textify

# Tried in this order, so an Impressum link beats a Kontakt link, and both
# beat Datenschutz. The prototype took whichever legal link came first on
# the page, and a privacy policy carries no UID.
_LEGAL_KINDS = (
    re.compile(r"impressum|imprint|mentions.legales|note.legali|legal.notice", re.I),
    re.compile(r"kontakt|contact|contatti", re.I),
    re.compile(r"datenschutz|legal|rechtlich", re.I),
)
_ABOUT = re.compile(r"ueber.uns|über.uns|about|a.propos|chi.siamo|wir.sind|team", re.I)

# Only when the home page links to none of the above.
_LEGAL_PATHS = ("/impressum", "/impressum.html", "/impressum.php", "/imprint")
_ABOUT_PATHS = ("/ueber-uns", "/about", "/a-propos", "/chi-siamo")

_LINK = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_JSON_LD = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)
_ORG_TYPES = {
    "Organization",
    "Corporation",
    "LocalBusiness",
    "ProfessionalService",
    "Store",
}

# What the model sees of each part, the prototype's split. One cap for the
# whole candidate let a long home page push the Impressum out of the prompt.
_PROMPT_CHARS = {"schema.org": 400, "home page": 1500, "impressum": 1000, "about": 800}


@dataclass(frozen=True)
class CandidatePages:
    home: str
    impressum: str = ""
    about: str = ""
    schema: str = ""

    def _parts(self) -> dict[str, str]:
        return {
            "schema.org": self.schema,
            "home page": self.home,
            "impressum": self.impressum,
            "about": self.about,
        }

    def full_text(self) -> str:
        """Everything, uncut. The UID check and the quote check run on this,
        so a UID sitting past the prompt's cut still counts."""
        return "\n\n".join(
            f"[{label}]\n{text}" for label, text in self._parts().items() if text
        )

    def for_prompt(self) -> str:
        """The same labels, each part cut to its own length."""
        return "\n\n".join(
            f"[{label}]\n{text[: _PROMPT_CHARS[label]]}"
            for label, text in self._parts().items()
            if text
        )


def _site(url: str) -> str:
    """Host without `www.` — `muster.ch` often redirects to `www.muster.ch`,
    and the links on the page then carry the `www`."""
    return (urlsplit(url).hostname or "").removeprefix("www.")


def find_links(html: str, base: str) -> tuple[list[str], list[str], list[str]]:
    """(Impressum links, other legal links best first, About links), all on
    the same site.

    A link is matched by its address or by its words, so `<a href="/r">
    Impressum</a>` is found as well as `/de/impressum`.
    """
    site = _site(base)
    ranked: list[tuple[int, str]] = []
    about: list[str] = []
    seen: set[str] = set()
    for href, inner in _LINK.findall(html):
        url = urljoin(base, href.strip())
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or _site(url) != site:
            continue
        if url in seen:
            continue
        seen.add(url)
        said = f"{href} {_TAG.sub(' ', inner)}"
        kind = next((i for i, rx in enumerate(_LEGAL_KINDS) if rx.search(said)), None)
        if kind is not None:
            ranked.append((kind, url))
        elif _ABOUT.search(said):
            about.append(url)
    impressum = [url for kind, url in ranked if kind == 0]
    # sorted() is stable: within one kind, links keep their page order.
    by_kind = sorted(ranked, key=lambda pair: pair[0])
    other = [url for kind, url in by_kind if kind > 0]
    return impressum[:3], other[:3], about[:3]


def read_schema_org(html: str) -> str:
    """`Name: … | Address: … | UID: …` for each organisation block, or ""."""
    lines: list[str] = []
    for block in _JSON_LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        flat: list = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                flat.extend(item["@graph"])
            else:
                flat.append(item)
        for item in flat:
            if not isinstance(item, dict):
                continue
            kinds = item.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if not _ORG_TYPES & set(kinds if isinstance(kinds, list) else []):
                continue
            name = item.get("legalName") or item.get("name") or ""
            place = item.get("address") or {}
            if isinstance(place, dict):
                place = " ".join(
                    str(place.get(key, ""))
                    for key in ("streetAddress", "postalCode", "addressLocality")
                    if place.get(key)
                )
            uid = item.get("taxID") or item.get("vatID") or ""
            fields = [("Name", name), ("Address", place), ("UID", uid)]
            line = " | ".join(f"{key}: {value}" for key, value in fields if value)
            if line:
                lines.append(line)
    return "\n".join(lines)


async def _first_with_text(urls: list[str], fetcher: Fetcher) -> str:
    """One after another, stopping at the first page that says anything.
    Same site, so they cannot run together without breaking the delay."""
    for url in urls:
        page = await fetcher.get(url)
        if page.html:
            text = textify(page.html)
            if text:
                return text
    return ""


async def read_candidate(url: str, *, fetcher: Fetcher) -> CandidatePages | None:
    """The four parts of one site, or None when its home page cannot be read."""
    home = await fetcher.get(url)
    if not home.html:
        return None
    home_text = textify(home.html)
    impressum_links, other_links, about = find_links(home.html, url)

    # A real Impressum link first, then the usual Impressum addresses, and
    # only then Kontakt or Datenschutz. Measured on the golden set: a home
    # page linking only its privacy policy still had an Impressum at
    # `/impressum`, and reading the policy instead lost the UID.
    impressum = (
        await _first_with_text(impressum_links, fetcher)
        or await _first_with_text(
            [urljoin(url, path) for path in _LEGAL_PATHS], fetcher
        )
        or await _first_with_text(other_links, fetcher)
    )
    about_text = await _first_with_text(about, fetcher) or await _first_with_text(
        [urljoin(url, path) for path in _ABOUT_PATHS], fetcher
    )
    return CandidatePages(
        home=home_text,
        impressum=impressum,
        about=about_text,
        schema=read_schema_org(home.html),
    )
