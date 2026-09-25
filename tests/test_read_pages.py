"""Reading the pages `pick_pages` chose.

Two things are worth testing here and one is not. The fetching itself has
`test_fetcher`; what this node adds is the "needs JavaScript" detector and
the rule that one unreadable page does not cost the other nine.

The detector's threshold comes from the probe (`research/website-reading.md`
§4): text under ~50 words *and* a framework mount point in the HTML. Both
halves matter — the probe's one near-empty site is a static one-page site
with almost nothing on it, not a JavaScript shell, and calling a browser for
it would be a second fetch that changes nothing.
"""

import httpx
import pytest
import respx

from company_reach.nodes.read_pages import needs_javascript, read_pages
from company_reach.settings import Settings
from company_reach.tools.db import connect, init_db
from company_reach.tools.fetcher import Fetcher

SITE = "https://muster-metallbau.ch"

REAL_PAGE = "<html><body><p>" + " ".join(["Metallbau"] * 120) + "</p></body></html>"

SHELL = """<html><body><div id="__next"></div>
<noscript>Bitte aktivieren Sie JavaScript.</noscript></body></html>"""

THIN_BUT_STATIC = """<html><body><h1>Muster Metallbau AG</h1>
<p>Beispielstrasse 1, 9000 St. Gallen. Telefon 071 000 00 00.</p>
</body></html>"""


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """The fetcher refuses a host that resolves to a private address, and in
    a test nothing resolves at all. `settings` comes from conftest."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)


@pytest.fixture(autouse=True)
def database(settings):
    """Production reaches this node with a database that already holds the
    pool, so the node does not defend against a missing one."""
    init_db(settings.db_path)


def quick(settings: Settings) -> Fetcher:
    """No politeness delay; the delay has its own test in test_fetcher."""
    return Fetcher(settings, delay_s=0.0)


def state(urls: list[str]) -> dict:
    return {"pages_to_read": urls}


def allow_robots() -> None:
    respx.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))


def serve(path: str, html: str = "", status: int = 200) -> None:
    respx.get(f"{SITE}{path}").mock(return_value=httpx.Response(status, html=html))


# --- the detector ------------------------------------------------------------


def test_a_framework_shell_needs_javascript():
    assert needs_javascript(SHELL, "")


def test_a_thin_but_static_page_does_not():
    """The probe's smallest site: 22 words, no mount point, nothing a browser
    would add. Zero of fifteen sites tripped the detector."""
    assert not needs_javascript(THIN_BUT_STATIC, "Muster Metallbau AG Beispielstrasse")


def test_a_full_page_does_not_even_with_a_mount_point():
    """A site can ship a bundler's markup and still serve its text. Wix does
    exactly that, and all three Wix sites in the probe were readable."""
    html = SHELL + REAL_PAGE
    assert not needs_javascript(html, " ".join(["Metallbau"] * 120))


# --- the node ----------------------------------------------------------------


@respx.mock
async def test_the_chosen_pages_come_back_as_text(settings):
    allow_robots()
    serve("/impressum", REAL_PAGE)
    serve("/team", REAL_PAGE)

    out = await read_pages(
        state([f"{SITE}/impressum", f"{SITE}/team"]),
        settings=settings,
        fetcher=quick(settings),
    )

    assert set(out["page_texts"]) == {f"{SITE}/impressum", f"{SITE}/team"}
    assert "Metallbau" in out["page_texts"][f"{SITE}/impressum"]


@respx.mock
async def test_one_unreadable_page_does_not_cost_the_others(settings):
    allow_robots()
    serve("/impressum", REAL_PAGE)
    serve("/gone", status=404)

    out = await read_pages(
        state([f"{SITE}/gone", f"{SITE}/impressum"]),
        settings=settings,
        fetcher=quick(settings),
    )

    assert list(out["page_texts"]) == [f"{SITE}/impressum"]


@respx.mock
async def test_a_page_is_truncated_at_a_word(settings, monkeypatch):
    """`max_chars_per_page` bounds what one page can spend of the extraction
    prompt. Cutting mid-word would hand the model half a name."""
    monkeypatch.setattr(settings, "max_chars_per_page", 40)
    allow_robots()
    serve("/impressum", REAL_PAGE)

    out = await read_pages(
        state([f"{SITE}/impressum"]), settings=settings, fetcher=quick(settings)
    )

    text = out["page_texts"][f"{SITE}/impressum"]
    assert len(text) <= 40
    assert not text.endswith("Metallba")


@respx.mock
async def test_the_footer_survives_the_cut_on_a_long_page(settings):
    """Review of E4: a one-page site with a long main text kept its footer
    after textify, and the 8,000-character cut then took it off the end."""
    allow_robots()
    main = "<p>" + "Wir fertigen Metallteile fuer den Maschinenbau. " * 300 + "</p>"
    footer = (
        "<footer><p>Inhaber: Hans Muster</p>"
        "<p>Beispielstrasse 1, 8000 Musterstadt</p>"
        "<p>UID: CHE-000.000.046</p></footer>"
    )
    serve("/", f"<html><body><main>{main}</main>{footer}</body></html>")

    out = await read_pages(
        state([f"{SITE}/"]), settings=settings, fetcher=quick(settings)
    )

    text = out["page_texts"][f"{SITE}/"]
    assert len(text) <= settings.max_chars_per_page
    for line in ("Hans Muster", "8000 Musterstadt", "CHE-000.000.046"):
        assert line in text
    assert "Metallteile" in text


@respx.mock
async def test_a_shell_is_recorded_as_needing_javascript(settings):
    """The counter the Playwright service is judged by. It travels with the
    company; totalling it over a run is #20's business."""
    allow_robots()
    serve("/", SHELL)

    out = await read_pages(
        state([f"{SITE}/"]), settings=settings, fetcher=quick(settings)
    )

    assert out["needs_js"] == [f"{SITE}/"]


@respx.mock
async def test_a_readable_site_records_nothing(settings):
    allow_robots()
    serve("/", REAL_PAGE)

    out = await read_pages(
        state([f"{SITE}/"]), settings=settings, fetcher=quick(settings)
    )

    assert out["needs_js"] == []


@respx.mock
async def test_what_was_read_is_indexed(settings):
    """The pages table has been in the schema since M1 with nothing filling
    it. M5 is the milestone that needs it: the review page and a later run
    should be able to see what the model was shown without re-fetching."""
    allow_robots()
    serve("/impressum", REAL_PAGE)
    serve("/team", REAL_PAGE)

    await read_pages(
        state([f"{SITE}/impressum", f"{SITE}/team"]),
        settings=settings,
        fetcher=quick(settings),
    )

    with connect(settings.db_path) as conn:
        rows = conn.execute("select * from pages order by url").fetchall()

    assert [r["url"] for r in rows] == [f"{SITE}/impressum", f"{SITE}/team"]
    assert all(r["status"] == 200 for r in rows)
    assert all("Metallbau" in r["text"] for r in rows)
    assert all(r["raw_path"].endswith(".html") for r in rows)


@respx.mock
async def test_a_page_that_could_not_be_read_is_not_indexed(settings):
    """The row would say we read it. An index of pages we never got is worse
    than no index."""
    allow_robots()
    serve("/gone", status=404)

    await read_pages(
        state([f"{SITE}/gone"]), settings=settings, fetcher=quick(settings)
    )

    with connect(settings.db_path) as conn:
        assert conn.execute("select count(*) from pages").fetchone()[0] == 0
