"""Finding a company's website, and proving it is that company's.

The milestone's judgement lives here. Three ways to believe a site belongs to
a company, from strongest to weakest: its register UID is on the page, its
register address is on the page, or the model read the page and said so with
a quote we then check. The model is asked in every case, because the golden
evaluation calls it in every case and a test that exercises a path production
does not take is measuring nothing.
"""

import httpx
import pytest
import respx

from company_reach.errors import SearchError
from company_reach.models import CompanyRecord
from company_reach.nodes import find_site as node
from company_reach.nodes.find_site import (
    build_queries,
    choose_candidates,
    dedupe_candidates,
    find_site,
    guess_domains,
    prune_page_urls,
)
from company_reach.settings import Settings
from company_reach.tools.fetcher import Fetcher
from company_reach.tools.search import Result

UID = "CHE000000046"
SITE = "https://muster-metallbau.ch"


def company(**over) -> CompanyRecord:
    base = dict(
        uid=UID,
        name="Muster Metallbau AG",
        legal_form="0106",
        municipality="3203",
        street="Beispielstrasse 1",
        postal_code="8000",
        city="Musterstadt",
        purpose="Herstellung und Verkauf von Metallteilen.",
        purpose_head="Herstellung und Verkauf von Metallteilen.",
    )
    return CompanyRecord(**(base | over))


# --- queries and the domain guess --------------------------------------------


def test_queries_strip_the_legal_form():
    queries = build_queries(company())
    assert '"Muster Metallbau" Musterstadt' == queries[0]
    assert "Muster Metallbau Impressum" == queries[1]
    assert all("AG" not in q.split() for q in queries)


def test_the_uid_is_queried_in_both_spellings():
    joined = " ".join(build_queries(company()))
    assert "CHE-000.000.046" in joined
    assert "CHE000000046" in joined


def test_a_company_in_liquidation_is_queried_by_its_name():
    queries = build_queries(company(name="Muster Metallbau AG in Liquidation"))
    assert "in Liquidation" not in queries[0]


def test_the_domain_guess_transliterates_the_german_way():
    """muller.ch, mueller.ch and müller.ch are three different hosts, and the
    second is the one Swiss firms actually register."""
    guesses = guess_domains("Müller Söhne AG")
    assert "muellersoehne.ch" in guesses
    assert "mueller-soehne.ch" in guesses


def test_the_domain_guess_stays_small():
    """Joined and hyphenated, .ch and .com. Four names, no more — each one
    costs a DNS lookup and a possible fetch."""
    assert len(guess_domains("Muster Metallbau AG")) == 4


def test_the_domain_guess_drops_punctuation():
    assert "musterco.ch" in guess_domains("Muster & Co. AG")


# --- candidate filtering -----------------------------------------------------


def test_blocklisted_results_never_become_candidates():
    results = [
        Result("https://www.linkedin.com/company/muster", "x", "y", "e"),
        Result("https://moneyhouse.ch/muster", "x", "y", "e"),
        Result(f"{SITE}/", "x", "y", "e"),
    ]
    assert dedupe_candidates(results) == [f"{SITE}/"]


def test_one_candidate_per_registered_domain():
    """Three pages of the same site are one candidate. Research: dedupe by
    host minus www."""
    results = [
        Result(f"{SITE}/", "x", "y", "e"),
        Result(f"{SITE}/impressum", "x", "y", "e"),
        Result("https://www.muster-metallbau.ch/kontakt", "x", "y", "e"),
    ]
    assert dedupe_candidates(results) == [f"{SITE}/"]


def test_non_http_results_are_dropped():
    results = [
        Result("javascript:alert(1)", "x", "y", "e"),
        Result(f"{SITE}/", "x", "y", "e"),
    ]
    assert dedupe_candidates(results) == [f"{SITE}/"]


def test_a_deep_page_becomes_its_site_root():
    """Search often finds a company through an inner page. The candidate is
    the site: its home page and Impressum are what get read."""
    results = [Result("https://www.muster-metallbau.ch/home/impressum/", "", "", "e")]
    assert dedupe_candidates(results) == ["https://www.muster-metallbau.ch/"]


# --- page listing ------------------------------------------------------------


def test_bulk_paths_go_first_when_pruning():
    urls = (
        [f"{SITE}/produkt/{n}" for n in range(300)]
        + [f"{SITE}/blog/2024/{n}" for n in range(300)]
        + [f"{SITE}/impressum", f"{SITE}/kontakt", f"{SITE}/ueber-uns"]
    )
    kept = prune_page_urls(urls, limit=200)
    assert f"{SITE}/impressum" in kept
    assert f"{SITE}/kontakt" in kept
    assert len(kept) == 200


def test_shallow_paths_are_preferred():
    urls = [f"{SITE}/a/b/c/d/e/{n}" for n in range(300)] + [f"{SITE}/kontakt"]
    assert prune_page_urls(urls, limit=5)[0] == f"{SITE}/kontakt"


def test_urls_are_normalised_and_deduped():
    urls = [f"{SITE}/kontakt", f"{SITE}/kontakt/", f"{SITE}/kontakt#adresse"]
    assert prune_page_urls(urls, limit=10) == [f"{SITE}/kontakt"]


def test_the_cap_is_respected_even_without_bulk_paths():
    assert len(prune_page_urls([f"{SITE}/p{n}" for n in range(500)], limit=200)) == 200


# --- the three tiers ---------------------------------------------------------

IMPRESSUM_WITH_UID = (
    "<html><body><div id='footer'><h2>Impressum</h2><p>Muster Metallbau AG<br>"
    "Beispielstrasse 1<br>8000 Musterstadt<br>CHE-000.000.046 MWST</p>"
    "</div></body></html>"
)
IMPRESSUM_WITH_ADDRESS = (
    "<html><body><div id='footer'><h2>Impressum</h2><p>Muster Metallbau AG<br>"
    "Beispielstrasse 1<br>8000 Musterstadt</p></div></body></html>"
)
IMPRESSUM_NEITHER = (
    "<html><body><div id='footer'><h2>Impressum</h2><p>Muster Metallbau AG<br>"
    "Andere Strasse 9<br>9999 Anderswo</p></div></body></html>"
)


@pytest.fixture
def wired(settings: Settings, monkeypatch):
    """Search returns one candidate; DNS resolves everything public; the
    domain guess finds nothing, so each test controls its own candidate."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    async def no_guesses(names, **kw):
        return []

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    monkeypatch.setattr(node, "resolving_domains", no_guesses)

    async def one_candidate(query, *, settings, limit=10):
        return [Result(f"{SITE}/", "Muster Metallbau AG", "Metallteile", "duckduckgo")]

    monkeypatch.setattr(node, "search", one_candidate)
    return settings


def quick(settings: Settings) -> Fetcher:
    """No politeness delay: the delay has its own test in test_fetcher."""
    return Fetcher(settings, delay_s=0.0)


def serve(html: str) -> None:
    respx.get("https://muster-metallbau.ch/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{SITE}/").mock(return_value=httpx.Response(200, html=html))
    respx.get(f"{SITE}/impressum").mock(return_value=httpx.Response(200, html=html))
    respx.get(f"{SITE}/sitemap.xml").mock(return_value=httpx.Response(404))
    # Every other page of the site — About, the fallback Impressum paths —
    # does not exist. Routes match in order, so this one comes last.
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))


def model_says(monkeypatch, chosen: str | None, quote: str | None = None) -> None:
    async def ask(prompt_name, output_model, *, settings, **variables):
        return output_model(chosen_url=chosen, quote=quote, reason="because"), None

    monkeypatch.setattr(node.llm, "ask", ask)


@respx.mock
async def test_a_uid_on_the_page_is_tier_one(wired, monkeypatch):
    serve(IMPRESSUM_WITH_UID)
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"].tier == "uid"
    assert out["site"].url == f"{SITE}/"


@respx.mock
async def test_the_register_address_is_tier_two(wired, monkeypatch):
    serve(IMPRESSUM_WITH_ADDRESS)
    model_says(monkeypatch, f"{SITE}/", "Beispielstrasse 1")
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"].tier == "address"


@respx.mock
async def test_neither_leaves_the_model_to_decide(wired, monkeypatch):
    serve(IMPRESSUM_NEITHER)
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"].tier == "model"
    assert out["site"].evidence == "Muster Metallbau AG"


@respx.mock
async def test_a_quote_that_is_not_on_the_page_is_rejected(wired, monkeypatch):
    """The quote is the whole point of tier 3: it proves the model read the
    page rather than guessed from the name."""
    serve(IMPRESSUM_NEITHER)
    model_says(monkeypatch, f"{SITE}/", "Wir sind seit 1920 Marktführer")
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"] is None
    assert out["recommendation"] == "skip"


@respx.mock
async def test_typography_does_not_break_a_true_quote(wired, monkeypatch):
    serve(IMPRESSUM_NEITHER)
    model_says(monkeypatch, f"{SITE}/", "Andere Strasse 9")
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"] is not None


# --- who wins when they disagree ---------------------------------------------


@respx.mock
async def test_the_model_can_veto_a_uid_match(wired, monkeypatch):
    """The first real run found this: a company directory publishes the UIDs
    of the firms it lists, so its page matches tier 1. The model saw a
    directory and said no, and the rejection has to stand — a UID proves the
    page is about the company, never that it belongs to the company."""
    serve(IMPRESSUM_WITH_UID)
    model_says(monkeypatch, None, None)
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"] is None
    assert out["recommendation"] == "skip"


@respx.mock
async def test_a_uid_still_decides_the_label(wired, monkeypatch):
    """Vetoing is not demotion: when the model does choose the page, the UID
    is still the strongest evidence the site can carry."""
    serve(IMPRESSUM_WITH_UID)
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"].tier == "uid"


@respx.mock
async def test_an_address_match_can_be_vetoed_too(wired, monkeypatch):
    serve(IMPRESSUM_WITH_ADDRESS)
    model_says(monkeypatch, None, None)
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"] is None
    assert out["recommendation"] == "skip"


# --- outcomes ----------------------------------------------------------------


def state(**over) -> dict:
    base = dict(run_id="r1", uid=UID, goal="g", about_me="a", company=company())
    return base | over


@respx.mock
async def test_no_candidate_at_all_is_a_finding_not_an_error(
    settings: Settings, monkeypatch
):
    """Five of twenty companies in the prototype had no findable website.
    That is a fact about the population."""

    async def nothing(query, *, settings, limit=10):
        return []

    async def no_guesses(names, **kw):
        return []

    monkeypatch.setattr(node, "search", nothing)
    monkeypatch.setattr(node, "resolving_domains", no_guesses)

    out = await find_site(state(), settings=settings, fetcher=quick(settings))
    assert out["site"] is None
    assert out["recommendation"] == "skip"
    assert "searches" in out["reason"] or "search" in out["reason"]


async def test_search_failing_is_an_error_not_a_finding(
    settings: Settings, monkeypatch
):
    """The difference the whole milestone turns on. 'We could not look' must
    never be written down as 'this company has no website'."""

    async def broken(query, *, settings, limit=10):
        raise SearchError("every baseline engine was unresponsive")

    monkeypatch.setattr(node, "search", broken)
    with pytest.raises(SearchError):
        await find_site(state(), settings=settings, fetcher=quick(settings))


@respx.mock
async def test_a_chosen_site_gets_its_pages_listed(wired, monkeypatch):
    respx.get("https://muster-metallbau.ch/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{SITE}/").mock(
        return_value=httpx.Response(200, html=IMPRESSUM_WITH_UID)
    )
    respx.get(f"{SITE}/impressum").mock(
        return_value=httpx.Response(200, html=IMPRESSUM_WITH_UID)
    )
    respx.get(f"{SITE}/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<urlset><url><loc>https://muster-metallbau.ch/kontakt</loc></url>"
                "<url><loc>https://muster-metallbau.ch/produkt/1</loc></url>"
                "</urlset>"
            ),
        )
    )
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")

    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert f"{SITE}/kontakt" in out["page_urls"]


@respx.mock
async def test_without_a_sitemap_the_home_page_links_are_used(wired, monkeypatch):
    html = (
        "<html><body><a href='/kontakt'>Kontakt</a>"
        "<a href='https://linkedin.com/x'>LI</a>"
        + IMPRESSUM_WITH_UID
        + "</body></html>"
    )
    respx.get("https://muster-metallbau.ch/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get(f"{SITE}/").mock(return_value=httpx.Response(200, html=html))
    respx.get(f"{SITE}/impressum").mock(return_value=httpx.Response(200, html=html))
    respx.get(f"{SITE}/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")

    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert f"{SITE}/kontakt" in out["page_urls"]
    assert not any("linkedin" in u for u in out["page_urls"])


@respx.mock
async def test_a_company_without_a_site_lists_no_pages(wired, monkeypatch):
    serve(IMPRESSUM_NEITHER)
    model_says(monkeypatch, None, None)
    out = await find_site(state(), settings=wired, fetcher=quick(wired))
    assert out["site"] is None
    assert "page_urls" not in out


# --- the fourth query --------------------------------------------------------


@respx.mock
async def test_a_fourth_query_runs_when_everything_was_a_directory(
    settings: Settings, monkeypatch
):
    """The research says not to *start* with `site:.ch`, not to avoid it.
    When the first three queries return only directories, narrowing to Swiss
    domains is the last cheap thing to try before giving up."""
    asked: list[str] = []

    async def searcher(query, *, settings, limit=10):
        asked.append(query)
        if "site:.ch" in query:
            return [Result(f"{SITE}/", "Muster Metallbau AG", "x", "ddg")]
        return [Result("https://moneyhouse.ch/muster", "x", "y", "ddg")]

    async def no_guesses(names, **kw):
        return []

    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    monkeypatch.setattr(node, "resolving_domains", no_guesses)
    monkeypatch.setattr(node, "search", searcher)
    serve(IMPRESSUM_WITH_UID)
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")

    out = await find_site(state(), settings=settings, fetcher=quick(settings))
    assert any("site:.ch" in q for q in asked)
    assert out["site"] is not None


@respx.mock
async def test_no_fourth_query_when_a_real_candidate_was_found(
    settings: Settings, monkeypatch
):
    """It is a last resort, not a fourth step. A candidate that is not a
    directory means the first three queries did their job."""
    asked: list[str] = []

    async def searcher(query, *, settings, limit=10):
        asked.append(query)
        return [Result(f"{SITE}/", "Muster Metallbau AG", "x", "ddg")]

    async def no_guesses(names, **kw):
        return []

    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    monkeypatch.setattr(node, "resolving_domains", no_guesses)
    monkeypatch.setattr(node, "search", searcher)
    serve(IMPRESSUM_WITH_UID)
    model_says(monkeypatch, f"{SITE}/", "Muster Metallbau AG")

    await find_site(state(), settings=settings, fetcher=quick(settings))
    assert not any("site:.ch" in q for q in asked)


async def test_domain_guesses_come_before_search_results(
    settings: Settings, monkeypatch
):
    """A resolving guess is built from the company's own name. Put after the
    search results, it lost its place to whatever search ranked first — on
    the golden set that cost the right site more than once."""

    async def searcher(query, *, settings, limit=10):
        return [Result(f"https://other-{i}.ch/", "", "", "ddg") for i in range(12)]

    async def guesses(names, **kw):
        return [f"{SITE}/"]

    monkeypatch.setattr(node, "resolving_domains", guesses)
    monkeypatch.setattr(node, "search", searcher)

    results = await node.search_results(company(), settings=settings)
    assert choose_candidates(results)[0] == f"{SITE}/"


async def test_a_guess_does_not_stop_the_fourth_query(settings: Settings, monkeypatch):
    """The fourth query asks whether *search* found anything but directories.
    A guess is not a search result, so it must not answer that question."""
    asked: list[str] = []

    async def searcher(query, *, settings, limit=10):
        asked.append(query)
        return [Result("https://moneyhouse.ch/muster", "x", "y", "ddg")]

    async def guesses(names, **kw):
        return [f"{SITE}/"]

    monkeypatch.setattr(node, "resolving_domains", guesses)
    monkeypatch.setattr(node, "search", searcher)

    await node.search_results(company(), settings=settings)
    assert any("site:.ch" in q for q in asked)


async def test_candidates_are_read_at_the_same_time():
    """Ten candidates read one after another made site choice the slowest
    step. They are different sites, so reading them together costs no
    politeness; the check is that requests to several hosts overlap."""
    import asyncio

    from company_reach.tools.fetcher import Page

    open_now, most = 0, 0

    class SlowFetcher:
        async def get(self, url):
            nonlocal open_now, most
            open_now += 1
            most = max(most, open_now)
            await asyncio.sleep(0.01)
            open_now -= 1
            return Page(url=url, html="<html><body><p>Muster AG</p></body></html>")

    sites = [f"https://site-{i}.ch/" for i in range(3)]
    texts = await node.read_candidates(sites, fetcher=SlowFetcher())
    assert list(texts) == sites
    assert most == 3


def test_a_quote_stitched_with_an_ellipsis_is_found():
    """Measured on the golden set: the model quotes the name, skips a line
    and quotes the UID, joined by '...'. Every piece is on the page, so the
    choice is grounded; rejecting it cost three right answers in twenty."""
    page = "Muster Metallbau AG\nBeispielstrasse 1\nTelefon 000\nUID: CHE-000.000.046"
    assert node.quote_found("Muster Metallbau AG ... UID: CHE-000.000.046", page)
    assert node.quote_found("Muster Metallbau AG … CHE-000.000.046", page)


def test_one_invented_piece_still_rejects_the_quote():
    page = "Muster Metallbau AG\nBeispielstrasse 1"
    assert not node.quote_found("Muster Metallbau AG ... UID: CHE-999.999.999", page)
    assert not node.quote_found(" ... ", page)
