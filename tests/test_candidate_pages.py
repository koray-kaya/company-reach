"""Reading what a candidate site says about itself.

The Impressum decides most site choices — it carries the UID and the
address — so most of these tests are about finding it where a real site
puts it rather than where we would guess.
"""

import httpx
import respx

from company_reach.settings import Settings
from company_reach.tools.candidate_pages import (
    CandidatePages,
    find_links,
    read_candidate,
    read_schema_org,
)
from company_reach.tools.fetcher import Fetcher

SITE = "https://muster-metallbau.ch/"


def test_an_impressum_link_beats_contact_and_privacy():
    """The prototype took the first legal link on the page. A privacy
    policy often comes first and never carries a UID."""
    html = (
        "<a href='/datenschutz'>Datenschutz</a>"
        "<a href='/kontakt'>Kontakt</a>"
        "<a href='/de/impressum'>Impressum</a>"
    )
    impressum, other, _ = find_links(html, SITE)
    assert impressum == [f"{SITE}de/impressum"]
    assert other == [f"{SITE}kontakt", f"{SITE}datenschutz"]


def test_a_link_is_found_by_its_words():
    html = "<a href='/rechtliches-1'><span>Impressum</span></a>"
    impressum, _, _ = find_links(html, SITE)
    assert impressum == [f"{SITE}rechtliches-1"]


def test_www_and_bare_host_are_one_site():
    """`muster.ch` redirects to `www.muster.ch`, and the page's links follow."""
    html = "<a href='https://www.muster-metallbau.ch/impressum'>Impressum</a>"
    impressum, _, _ = find_links(html, SITE)
    assert impressum == ["https://www.muster-metallbau.ch/impressum"]


def test_links_to_other_sites_are_ignored():
    html = "<a href='https://moneyhouse.ch/impressum'>Impressum</a>"
    assert find_links(html, SITE) == ([], [], [])


def test_about_links_are_kept_apart():
    html = "<a href='/ueber-uns'>Über uns</a><a href='/impressum'>Impressum</a>"
    impressum, _, about = find_links(html, SITE)
    assert impressum == [f"{SITE}impressum"]
    assert about == [f"{SITE}ueber-uns"]


def test_schema_org_gives_name_address_and_uid():
    html = (
        '<script type="application/ld+json">{"@context": "https://schema.org",'
        '"@graph": [{"@type": "Organization", "legalName": "Muster Metallbau AG",'
        '"address": {"streetAddress": "Beispielstrasse 1", "postalCode": "8000",'
        '"addressLocality": "Musterstadt"}, "vatID": "CHE-000.000.046"}]}</script>'
    )
    assert read_schema_org(html) == (
        "Name: Muster Metallbau AG | Address: Beispielstrasse 1 8000 Musterstadt"
        " | UID: CHE-000.000.046"
    )


def test_broken_or_unrelated_schema_org_is_skipped():
    html = (
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">{"@type": "WebPage", "name": "x"}'
        "</script>"
    )
    assert read_schema_org(html) == ""


def test_a_long_home_page_cannot_push_the_impressum_out():
    """With one cap per candidate, 1,500 words of home page left no room for
    the Impressum. Each part now has its own."""
    pages = CandidatePages(home="Werbung " * 1000, impressum="CHE-000.000.046")
    assert "CHE-000.000.046" in pages.for_prompt()
    assert len(pages.for_prompt()) < 3000


def test_the_full_text_is_not_cut():
    """The UID and quote checks see everything, not the prompt's cut."""
    pages = CandidatePages(home="x", impressum="Text " * 500 + "CHE-000.000.046")
    assert "CHE-000.000.046" in pages.full_text()
    assert "CHE-000.000.046" not in pages.for_prompt()


def _page(body: str) -> httpx.Response:
    return httpx.Response(200, html=f"<html><body><p>{body}</p></body></html>")


@respx.mock
async def test_the_impressum_is_read_from_the_link(settings: Settings, monkeypatch):
    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    respx.get(f"{SITE}robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SITE).mock(
        return_value=httpx.Response(
            200, html="<p>Willkommen</p><a href='/de/impressum'>Impressum</a>"
        )
    )
    respx.get(f"{SITE}de/impressum").mock(return_value=_page("CHE-000.000.046"))
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))

    pages = await read_candidate(SITE, fetcher=Fetcher(settings, delay_s=0.0))
    assert pages is not None
    assert "CHE-000.000.046" in pages.impressum


@respx.mock
async def test_without_a_link_the_fixed_paths_are_tried(
    settings: Settings, monkeypatch
):
    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    respx.get(f"{SITE}robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SITE).mock(return_value=_page("Willkommen"))
    respx.get(f"{SITE}impressum.html").mock(return_value=_page("CHE-000.000.046"))
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))

    pages = await read_candidate(SITE, fetcher=Fetcher(settings, delay_s=0.0))
    assert pages is not None
    assert "CHE-000.000.046" in pages.impressum


@respx.mock
async def test_the_impressum_path_is_tried_before_a_privacy_link(
    settings: Settings, monkeypatch
):
    """Seen on a real site: the home page linked only its privacy policy,
    and the Impressum with the UID sat at `/impressum` all along."""

    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    respx.get(f"{SITE}robots.txt").mock(return_value=httpx.Response(404))
    respx.get(SITE).mock(
        return_value=httpx.Response(
            200, html="<p>Willkommen</p><a href='datenschutz.html'>Datenschutz</a>"
        )
    )
    respx.get(f"{SITE}datenschutz.html").mock(return_value=_page("Wir schützen"))
    respx.get(f"{SITE}impressum").mock(return_value=_page("CHE-000.000.046"))
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))

    pages = await read_candidate(SITE, fetcher=Fetcher(settings, delay_s=0.0))
    assert pages is not None
    assert "CHE-000.000.046" in pages.impressum


@respx.mock
async def test_an_unreachable_home_page_is_no_candidate(
    settings: Settings, monkeypatch
):
    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))

    assert await read_candidate(SITE, fetcher=Fetcher(settings, delay_s=0.0)) is None
