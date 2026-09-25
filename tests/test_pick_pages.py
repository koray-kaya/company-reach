"""Choosing which of a site's pages are worth reading.

The node's whole job is to hand `read_pages` a short list, so the tests are
about what it refuses to pass on: a URL the model invented, more than the
cap, and — the cheapest case — a site small enough that there is nothing to
choose. And about what it never leaves out: the home page and the pages that
name people, whatever the model answers.
"""

import pytest

from company_reach.models import CompanyRecord
from company_reach.nodes import pick_pages as node
from company_reach.nodes.find_site import SiteChoice
from company_reach.nodes.pick_pages import pick_pages
from company_reach.settings import Settings

SITE = "https://muster-metallbau.ch"


def company() -> CompanyRecord:
    return CompanyRecord(
        uid="CHE000000046",
        name="Muster Metallbau AG",
        legal_form="0106",
        municipality="3203",
        city="St. Gallen",
        purpose="Metallbau und Schlosserei.",
        purpose_head="Metallbau und Schlosserei.",
    )


def state(urls: list[str]) -> dict:
    return {
        "company": company(),
        "site": SiteChoice(SITE, "uid", "CHE-000.000.046", SITE),
        "page_urls": urls,
    }


def model_returns(monkeypatch, urls: list[str]) -> list[str]:
    """Stubs the model and returns a list that records every call, so a test
    can assert the model was not asked at all."""
    calls: list[str] = []

    async def ask(prompt_name, output_model, *, settings, **variables):
        calls.append(prompt_name)
        return output_model(urls=urls), None

    monkeypatch.setattr(node.llm, "ask", ask)
    return calls


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    return Settings(_env_file=None, data_dir=tmp_path / "data")


async def test_a_short_list_is_read_whole_without_asking(settings, monkeypatch):
    """Under the cap the answer cannot be wrong, so the call is not made.
    Most sites in this population are this small."""
    calls = model_returns(monkeypatch, [])
    urls = [f"{SITE}/", f"{SITE}/impressum", f"{SITE}/kontakt"]

    out = await pick_pages(state(urls), settings=settings)

    assert out["pages_to_read"] == urls
    assert calls == []


async def test_a_long_list_is_handed_to_the_model(settings, monkeypatch):
    calls = model_returns(monkeypatch, [f"{SITE}/impressum", f"{SITE}/team"])
    urls = [f"{SITE}/p{n}" for n in range(30)] + [f"{SITE}/impressum", f"{SITE}/team"]

    out = await pick_pages(state(urls), settings=settings)

    assert calls == ["pick_pages"]
    assert out["pages_to_read"] == [SITE, f"{SITE}/impressum", f"{SITE}/team"]


async def test_a_url_the_model_did_not_get_offered_is_dropped(settings, monkeypatch):
    """The same guard `score_pool` puts on returned UIDs. A page invented by
    the model — or planted by one the site controls — is not fetched."""
    model_returns(
        monkeypatch, [f"{SITE}/impressum", "https://evil.example/pwn", f"{SITE}/nope"]
    )
    urls = [f"{SITE}/p{n}" for n in range(30)] + [f"{SITE}/impressum"]

    out = await pick_pages(state(urls), settings=settings)

    assert out["pages_to_read"] == [SITE, f"{SITE}/impressum"]


async def test_an_empty_answer_still_reads_the_home_page(settings, monkeypatch):
    """Answering "none of these" means something different here than it does
    for site choice: there the question is whether the company has a site at
    all, while by this point the site is verified. Reading nothing would
    throw away a company we know we found."""
    model_returns(monkeypatch, [])
    urls = [f"{SITE}/p{n}" for n in range(30)]

    out = await pick_pages(state(urls), settings=settings)

    assert out["pages_to_read"] == [SITE]


async def test_more_pages_than_the_cap_are_trimmed(settings, monkeypatch):
    monkeypatch.setattr(settings, "max_pages_per_site", 3)
    offered = [f"{SITE}/p{n}" for n in range(30)]
    model_returns(monkeypatch, offered[:9])

    out = await pick_pages(state(offered), settings=settings)

    assert out["pages_to_read"] == [SITE, *offered[:2]]


async def test_a_site_with_one_page_asks_nobody(settings, monkeypatch):
    calls = model_returns(monkeypatch, [])
    out = await pick_pages(state([SITE]), settings=settings)
    assert out["pages_to_read"] == [SITE]
    assert calls == []


# --- the floor ---------------------------------------------------------------
# Whether the Impressum was read depended on the model alone: offered the
# home page, /impressum, /kontakt and /team among twenty product pages, a
# model answering ten product pages left the profile with nobody named
# (audit). The pages `find_site` reads for the same reason are read here too.

PRODUCTS = [f"{SITE}/produkte/p{n}" for n in range(20)]
FLOOR = [f"{SITE}/", f"{SITE}/impressum", f"{SITE}/kontakt", f"{SITE}/team"]


async def test_the_impressum_is_always_read(settings, monkeypatch):
    model_returns(monkeypatch, PRODUCTS[:10])

    out = await pick_pages(state(PRODUCTS + FLOOR), settings=settings)

    assert out["pages_to_read"] == FLOOR + PRODUCTS[:6]


async def test_a_page_the_floor_has_is_not_read_twice(settings, monkeypatch):
    model_returns(monkeypatch, [f"{SITE}/impressum", PRODUCTS[0]])

    out = await pick_pages(state(PRODUCTS + FLOOR), settings=settings)

    assert out["pages_to_read"] == [*FLOOR, PRODUCTS[0]]


async def test_an_ueber_uns_page_is_part_of_the_floor(settings, monkeypatch):
    model_returns(monkeypatch, [])
    about = f"{SITE}/unternehmen/ueber-uns"

    out = await pick_pages(state([*PRODUCTS, about]), settings=settings)

    assert out["pages_to_read"] == [SITE, about]


async def test_a_floor_that_fills_the_cap_asks_nobody(settings, monkeypatch):
    monkeypatch.setattr(settings, "max_pages_per_site", 3)
    calls = model_returns(monkeypatch, PRODUCTS[:3])

    out = await pick_pages(state(PRODUCTS + FLOOR), settings=settings)

    assert out["pages_to_read"] == FLOOR[:3]
    assert calls == []


# One of each kind, not every language's copy: a site in four languages
# filled the cap with its own Impressum and left the model nothing (review).


def model_sees(monkeypatch, urls: list[str]) -> dict:
    """Stubs the model and keeps what it was shown."""
    shown: dict = {}

    async def ask(prompt_name, output_model, *, settings, **variables):
        shown.update(variables)
        return output_model(urls=urls), None

    monkeypatch.setattr(node.llm, "ask", ask)
    return shown


def languages(*codes: str) -> list[str]:
    paths = {
        "de": ("impressum", "kontakt", "ueber-uns"),
        "fr": ("mentions-legales", "contact", "a-propos"),
        "it": ("note-legali", "contatti", "chi-siamo"),
        "en": ("imprint", "contact", "about-us"),
    }
    return [f"{SITE}/{code}/{path}" for code in codes for path in paths[code]]


async def test_a_two_language_site_leaves_slots_for_the_model(settings, monkeypatch):
    shown = model_sees(monkeypatch, PRODUCTS[:10])
    pages = languages("de", "fr")

    out = await pick_pages(state([f"{SITE}/", *pages, *PRODUCTS]), settings=settings)

    # the home page and the first offered of each kind; six slots are left
    assert out["pages_to_read"] == [f"{SITE}/", *pages[:3], *PRODUCTS[:6]]
    assert shown["limit"] == "6"


async def test_a_four_language_site_leaves_slots_for_the_model(settings, monkeypatch):
    shown = model_sees(monkeypatch, PRODUCTS[:10])
    pages = languages("de", "fr", "it", "en")

    out = await pick_pages(state([f"{SITE}/", *pages, *PRODUCTS]), settings=settings)

    assert out["pages_to_read"] == [f"{SITE}/", *pages[:3], *PRODUCTS[:6]]
    assert shown["limit"] == "6"


async def test_the_floor_is_not_offered_to_the_model_again(settings, monkeypatch):
    shown = model_sees(monkeypatch, [])
    pages = languages("de", "fr")

    await pick_pages(state([f"{SITE}/", *pages, *PRODUCTS]), settings=settings)

    offered = shown["pages"]
    assert f"{SITE}/de/impressum" not in offered
    assert f"{SITE}/\n" not in offered
    assert f"{SITE}/fr/mentions-legales" in offered  # another copy stays choosable


@pytest.mark.parametrize(
    "path",
    [
        "/shop/kontaktlinsen",
        "/produkte/steamer-pro",
        "/teamsport",
        "/blog/roundabout",
    ],
)
async def test_a_word_inside_a_product_path_is_not_a_floor_page(
    settings, monkeypatch, path
):
    model_returns(monkeypatch, [])

    out = await pick_pages(state([*PRODUCTS, f"{SITE}{path}"]), settings=settings)

    assert out["pages_to_read"] == [SITE]
