"""The fetcher, which is where this project first touches the open internet.

Two audit items live here (A5): the URL scheme allowlist and the SSRF guard.
Both protect against the same shape of problem — a URL we did not choose,
arriving from a search engine, pointed somewhere we never meant to go.
"""

import httpx
import pytest
import respx

from company_reach.errors import FetchError
from company_reach.settings import Settings
from company_reach.tools import fetcher as fetcher_module
from company_reach.tools.fetcher import Fetcher

HOME = "https://muster-metallbau.ch/"
ROBOTS = "https://muster-metallbau.ch/robots.txt"


@pytest.fixture
def f(settings: Settings, monkeypatch) -> Fetcher:
    """No real DNS, no real sleeping. Every host resolves to a public
    address unless a test says otherwise."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetcher_module, "resolve_host", resolve)
    return Fetcher(settings, delay_s=0.0)


def allow_robots() -> None:
    respx.get(ROBOTS).mock(return_value=httpx.Response(200, text="User-agent: *\n"))


# --- the scheme allowlist ----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<h1>x</h1>",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
async def test_only_http_and_https_are_fetched(f: Fetcher, url: str):
    """Refused before any request is made — a search result can carry these."""
    page = await f.get(url)
    assert page.error is not None
    assert "scheme" in page.error


# --- the SSRF guard ----------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
async def test_private_addresses_are_refused(f: Fetcher, url: str):
    page = await f.get(url)
    assert page.error is not None
    assert "private" in page.error or "not public" in page.error


async def test_a_public_name_resolving_to_a_private_address_is_refused(
    f: Fetcher, monkeypatch
):
    """The guard has to run after DNS, not on the text of the URL. A name
    that looks ordinary can resolve into the private range, which is how the
    cloud metadata endpoint gets reached."""

    async def resolve(host: str) -> list[str]:
        return ["169.254.169.254"]

    monkeypatch.setattr(fetcher_module, "resolve_host", resolve)
    page = await f.get("https://innocent-looking.example/")
    assert page.error is not None
    assert "not public" in page.error or "private" in page.error


async def test_searxng_itself_cannot_be_fetched(f: Fetcher, monkeypatch):
    """The concrete case from the audit: a result pointing at our own
    container network."""

    async def resolve(host: str) -> list[str]:
        return ["172.18.0.2"]

    monkeypatch.setattr(fetcher_module, "resolve_host", resolve)
    assert (await f.get("http://searxng:8080/")).error is not None


# --- ordinary fetching -------------------------------------------------------


@respx.mock
async def test_a_page_comes_back_with_its_html(f: Fetcher):
    allow_robots()
    respx.get(HOME).mock(return_value=httpx.Response(200, html="<h1>Muster</h1>"))
    page = await f.get(HOME)
    assert page.status == 200
    assert "Muster" in page.html
    assert page.error is None


@respx.mock
async def test_a_failed_page_does_not_raise(f: Fetcher):
    """One page failing is normal. The company may still be findable from
    another page, so this returns a Page carrying the error."""
    allow_robots()
    respx.get(HOME).mock(return_value=httpx.Response(404))
    page = await f.get(HOME)
    assert page.status == 404
    assert page.error is not None
    assert page.html == ""


@respx.mock
async def test_a_transport_error_does_not_raise_either(f: Fetcher):
    allow_robots()
    respx.get(HOME).mock(side_effect=httpx.ConnectError("refused"))
    page = await f.get(HOME)
    assert page.error is not None
    assert page.unreachable


async def test_a_name_that_does_not_resolve_is_unreachable(f: Fetcher, monkeypatch):
    """DNS failing is not a refusal of ours: it may pass, so the page is
    marked the way a timeout is."""

    async def fails(host: str) -> list[str]:
        raise OSError("temporary failure in name resolution")

    monkeypatch.setattr(fetcher_module, "resolve_host", fails)
    page = await f.get(HOME)
    assert page.unreachable


async def test_our_own_refusal_is_not_unreachable(f: Fetcher, monkeypatch):
    async def private(host: str) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(fetcher_module, "resolve_host", private)
    page = await f.get(HOME)
    assert page.error is not None
    assert not page.unreachable


@respx.mock
async def test_a_response_over_the_cap_is_dropped(f: Fetcher, settings: Settings):
    """A Wix home page is 0.8-1.5 MB of HTML; two megabytes is generous and
    still bounds what one bad URL can cost us."""
    allow_robots()
    big = "x" * (settings.max_page_bytes + 1)
    respx.get(HOME).mock(return_value=httpx.Response(200, html=big))
    page = await f.get(HOME)
    assert page.html == ""
    assert page.error is not None
    assert "large" in page.error


# --- robots.txt --------------------------------------------------------------


@respx.mock
async def test_a_disallowed_url_is_skipped_without_raising(f: Fetcher):
    """Neither an error nor a finding: 'no site' is a statement about the
    company, and one refused page is not that."""
    respx.get(ROBOTS).mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /impressum")
    )
    route = respx.get("https://muster-metallbau.ch/impressum")
    page = await f.get("https://muster-metallbau.ch/impressum")
    assert page.error is not None
    assert "robots" in page.error
    assert not route.called


@respx.mock
async def test_a_missing_robots_file_allows_everything(f: Fetcher):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(HOME).mock(return_value=httpx.Response(200, html="<p>ok</p>"))
    assert (await f.get(HOME)).status == 200


@respx.mock
async def test_robots_is_read_once_per_host(f: Fetcher):
    allow_robots()
    respx.get(HOME).mock(return_value=httpx.Response(200, html="<p>ok</p>"))
    respx.get("https://muster-metallbau.ch/kontakt").mock(
        return_value=httpx.Response(200, html="<p>ok</p>")
    )
    await f.get(HOME)
    await f.get("https://muster-metallbau.ch/kontakt")
    assert respx.get(ROBOTS).call_count == 1


@respx.mock
async def test_a_declared_crawl_delay_is_honoured(settings: Settings, monkeypatch):
    """The probe saw sites declaring 1, 3 and 5 seconds. A declared delay
    replaces our default rather than adding to it."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetcher_module, "resolve_host", resolve)
    monkeypatch.setattr(fetcher_module.asyncio, "sleep", fake_sleep)

    respx.get(ROBOTS).mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 5")
    )
    respx.get(HOME).mock(return_value=httpx.Response(200, html="<p>ok</p>"))
    respx.get("https://muster-metallbau.ch/kontakt").mock(
        return_value=httpx.Response(200, html="<p>ok</p>")
    )

    fetch = Fetcher(settings, delay_s=1.0)
    await fetch.get(HOME)
    await fetch.get("https://muster-metallbau.ch/kontakt")
    assert 5 in slept


# --- the cache ---------------------------------------------------------------


@respx.mock
async def test_a_page_is_fetched_once(f: Fetcher):
    allow_robots()
    route = respx.get(HOME).mock(return_value=httpx.Response(200, html="<p>first</p>"))
    await f.get(HOME)
    page = await f.get(HOME)
    assert route.call_count == 1
    assert "first" in page.html


@respx.mock
async def test_refetch_goes_back_to_the_network(settings: Settings, monkeypatch):
    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(fetcher_module, "resolve_host", resolve)
    allow_robots()
    route = respx.get(HOME).mock(return_value=httpx.Response(200, html="<p>a</p>"))

    await Fetcher(settings, delay_s=0.0).get(HOME)
    await Fetcher(settings, delay_s=0.0, refetch=True).get(HOME)
    assert route.call_count == 2


@respx.mock
async def test_a_failure_is_not_cached(f: Fetcher):
    """Caching a 404 would make a transient outage permanent."""
    allow_robots()
    route = respx.get(HOME).mock(return_value=httpx.Response(503))
    await f.get(HOME)
    await f.get(HOME)
    assert route.call_count == 2


# --- the home page is different ----------------------------------------------


@respx.mock
async def test_an_unreachable_home_page_raises_after_retries(f: Fetcher):
    """This is the one case that is infrastructure rather than a finding:
    if we cannot reach the site at all, we have not learned that the company
    has no site."""
    allow_robots()
    route = respx.get(HOME).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(FetchError):
        await f.get_home(HOME)
    assert route.call_count == 2


@respx.mock
async def test_a_home_page_that_answers_does_not_raise(f: Fetcher):
    allow_robots()
    respx.get(HOME).mock(return_value=httpx.Response(200, html="<p>ok</p>"))
    assert (await f.get_home(HOME)).status == 200
