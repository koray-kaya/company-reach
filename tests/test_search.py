"""Search, and the one distinction the whole milestone rests on.

An empty result list is usually an answer: this company has no website. But
if every engine that could have answered was suspended, the same empty list
means nothing was asked. Recording the first as a finding is right; recording
the second as a finding is a lie about the company, and it burns a candidate
that will never be looked at again.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from pydantic import SecretStr

from company_reach.errors import SearchError
from company_reach.nodes.probe_search import probe_search
from company_reach.settings import Settings
from company_reach.tools import search as search_module
from company_reach.tools.search import Result, _brave, search

SEARXNG = "http://searxng:8080/search"
BRAVE = "https://api.search.brave.com/res/v1/web/search"


@pytest.fixture
def s(settings: Settings, monkeypatch) -> Settings:
    """No politeness sleeping in tests; the gap has its own test."""
    monkeypatch.setattr(search_module.asyncio, "sleep", _no_sleep)
    return settings


async def _no_sleep(seconds: float) -> None:
    return None


def searxng_body(results: list[dict], unresponsive: list | None = None) -> dict:
    return {"results": results, "unresponsive_engines": unresponsive or []}


def keyed(settings: Settings) -> Settings:
    """The same settings with a Brave key: the paid provider is armed."""
    return settings.model_copy(
        update={"brave_search_api_key": SecretStr("brave-test-key")}
    )


def brave_body(*urls: str) -> dict:
    return {
        "web": {
            "results": [
                {
                    "url": url,
                    "title": "Muster Metallbau AG",
                    "description": "Metallbau in Musterstadt.",
                }
                for url in urls
            ]
        }
    }


def one_result(url: str = "https://muster-metallbau.ch/") -> dict:
    return {
        "url": url,
        "title": "Muster Metallbau AG",
        "content": "Wir fertigen Metallteile.",
        "engine": "duckduckgo",
    }


# --- the ordinary path -------------------------------------------------------


@respx.mock
async def test_results_are_mapped(s: Settings):
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    found = await search("Muster Metallbau", settings=s)
    assert found == [
        Result(
            url="https://muster-metallbau.ch/",
            title="Muster Metallbau AG",
            snippet="Wir fertigen Metallteile.",
            engine="duckduckgo",
        )
    ]


@respx.mock
async def test_an_empty_result_is_an_answer_not_an_error(s: Settings):
    """The engines worked and found nothing. That is a fact about the
    company, and it is the finding the whole milestone exists to produce."""
    respx.get(SEARXNG).mock(return_value=httpx.Response(200, json=searxng_body([])))
    assert await search("Firma ohne Website", settings=s) == []


# --- when the empty list is not an answer ------------------------------------


@respx.mock
async def test_searxng_unreachable_raises(s: Settings):
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SearchError):
        await search("anything", settings=s)


@respx.mock
async def test_an_http_error_from_searxng_raises(s: Settings):
    respx.get(SEARXNG).mock(return_value=httpx.Response(500))
    with pytest.raises(SearchError):
        await search("anything", settings=s)


@respx.mock
async def test_empty_with_every_baseline_engine_down_raises(s: Settings):
    """Nothing was asked. Reporting 'no website' here would be a claim we
    have no evidence for."""
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(
            200,
            json=searxng_body(
                [],
                [
                    ["duckduckgo", "timeout"],
                    ["mojeek", "timeout"],
                    ["brave", "CAPTCHA"],
                ],
            ),
        )
    )
    with pytest.raises(SearchError):
        await search("anything", settings=s)


@respx.mock
async def test_empty_with_one_baseline_engine_down_does_not_raise(s: Settings):
    """Suspensions are the normal weather — the research recorded timers
    from three minutes to fifteen days. One engine out is not an outage."""
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(
            200, json=searxng_body([], [["brave", "suspended"]])
        )
    )
    assert await search("anything", settings=s) == []


@respx.mock
async def test_results_present_never_raise_whatever_is_unresponsive(s: Settings):
    """If something came back, something answered. Which engines were down
    is then irrelevant."""
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(
            200,
            json=searxng_body(
                [one_result()],
                [["duckduckgo", "x"], ["mojeek", "x"], ["brave", "x"]],
            ),
        )
    )
    assert len(await search("anything", settings=s)) == 1


# --- without a paid provider -------------------------------------------------


@respx.mock
async def test_without_a_key_the_search_error_stands(s: Settings):
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SearchError):
        await search("anything", settings=s)


# --- the Brave Search API ----------------------------------------------------


@respx.mock
async def test_brave_results_map_to_result(s: Settings):
    route = respx.get(BRAVE).mock(
        return_value=httpx.Response(
            200, json=brave_body("https://muster-metallbau.ch/")
        )
    )
    found = await _brave("Muster Metallbau", settings=keyed(s), limit=10)
    assert found == [
        Result(
            url="https://muster-metallbau.ch/",
            title="Muster Metallbau AG",
            snippet="Metallbau in Musterstadt.",
            engine="brave-api",
            provider="brave",
        )
    ]
    sent = route.calls.last.request
    assert sent.headers["X-Subscription-Token"] == "brave-test-key"
    assert sent.headers["Accept"] == "application/json"
    assert dict(sent.url.params) == {
        "q": "Muster Metallbau",
        "count": "10",
        "country": "CH",
        "search_lang": "de",
    }


@respx.mock
async def test_brave_is_asked_for_at_most_twenty(s: Settings):
    """Twenty is Brave's own ceiling for one request."""
    route = respx.get(BRAVE).mock(return_value=httpx.Response(200, json=brave_body()))
    await _brave("Muster Metallbau", settings=keyed(s), limit=50)
    assert route.calls.last.request.url.params["count"] == "20"


@pytest.mark.parametrize("status", [402, 429, 500, 503])
@respx.mock
async def test_brave_429_is_an_error_not_empty(s: Settings, status: int):
    """The earlier project's lesson: a Brave 5xx came back as `[]`, and
    quality dropped for weeks before anyone noticed. An empty list must only
    ever mean "Brave looked and found nothing"."""
    respx.get(BRAVE).mock(return_value=httpx.Response(status))
    with pytest.raises(SearchError, match=f"HTTP {status}"):
        await _brave("Muster Metallbau", settings=keyed(s), limit=10)


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_brave_401_names_the_key(s: Settings, status: int):
    respx.get(BRAVE).mock(return_value=httpx.Response(status))
    with pytest.raises(SearchError, match="BRAVE_SEARCH_API_KEY"):
        await _brave("Muster Metallbau", settings=keyed(s), limit=10)


@respx.mock
async def test_brave_unreachable_is_an_error(s: Settings):
    respx.get(BRAVE).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SearchError, match="Brave unreachable"):
        await _brave("Muster Metallbau", settings=keyed(s), limit=10)


@respx.mock
async def test_brave_is_never_asked_without_a_key(s: Settings):
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    route = respx.get(BRAVE)
    with pytest.raises(SearchError):
        await search("anything", settings=s)
    with pytest.raises(SearchError, match="not configured"):
        await _brave("anything", settings=s, limit=10)
    assert not route.called


@respx.mock
async def test_brave_takes_over_after_a_search_error(s: Settings):
    respx.get(SEARXNG).mock(return_value=httpx.Response(503))
    respx.get(BRAVE).mock(
        return_value=httpx.Response(
            200, json=brave_body("https://muster-metallbau.ch/")
        )
    )
    found = await search("anything", settings=keyed(s))
    assert [(r.url, r.provider) for r in found] == [
        ("https://muster-metallbau.ch/", "brave")
    ]


@respx.mock
async def test_brave_is_not_asked_for_an_empty_result(s: Settings):
    """Asking on every empty list would spend paid queries on exactly the
    companies that have no website. find_site decides when silence is
    suspicious; `search` only replaces a provider that failed."""
    respx.get(SEARXNG).mock(return_value=httpx.Response(200, json=searxng_body([])))
    route = respx.get(BRAVE)
    assert await search("anything", settings=keyed(s)) == []
    assert not route.called


@respx.mock
async def test_brave_is_not_asked_when_searxng_answers(s: Settings):
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    route = respx.get(BRAVE)
    await search("anything", settings=keyed(s))
    assert not route.called


@respx.mock
async def test_both_failing_names_both(s: Settings):
    respx.get(SEARXNG).mock(return_value=httpx.Response(503))
    respx.get(BRAVE).mock(return_value=httpx.Response(429))
    with pytest.raises(SearchError) as caught:
        await search("anything", settings=keyed(s))
    assert "503" in str(caught.value)
    assert "429" in str(caught.value)


# --- rate control ------------------------------------------------------------


@respx.mock
async def test_queries_in_flight_stay_under_the_cap(s: Settings):
    """Ten children starting find_site at once would fire thirty to forty
    queries from one IP in the first seconds — the pattern that gets an
    engine suspended."""
    in_flight = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return httpx.Response(200, json=searxng_body([one_result()]))

    respx.get(SEARXNG).mock(side_effect=slow)
    await asyncio.gather(*[search(f"q{n}", settings=s) for n in range(6)])
    assert peak <= s.search_concurrency


@respx.mock
async def test_a_gap_is_left_between_queries(settings: Settings, monkeypatch):
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(search_module.asyncio, "sleep", record)
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    await search("one", settings=settings)
    assert slept and slept[-1] >= 1.0


def test_search_is_paced_for_one_ip(settings: Settings):
    """The round-1 live run had two queries in flight and a one-second gap,
    and DuckDuckGo and Brave were suspended within one batch."""
    assert settings.search_concurrency == 1
    assert settings.search_gap_s == 2.0


def test_settings_yml_activates_the_baseline_engines(settings: Settings):
    """Upstream marks mojeek and startpage `inactive` at the pinned image, and
    `use_default_settings` inherits that for any engine that does not say
    otherwise — so doctor reported mojeek missing, and the guard for "every
    baseline engine is down" could never fire. An absent key is not enough:
    each baseline engine has to say `inactive: false` itself."""
    path = Path(__file__).parent.parent / "searxng" / "settings.yml"
    engines = {e["name"]: e for e in yaml.safe_load(path.read_text())["engines"]}
    baseline = [n.strip() for n in settings.baseline_engines.split(",") if n.strip()]
    for name in baseline:
        assert name in engines, name
        assert engines[name].get("inactive") is False, name
        assert engines[name].get("disabled") is not True, name


# --- probe_search ------------------------------------------------------------


@respx.mock
async def test_the_probe_passes_when_search_answers(s: Settings):
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    assert await probe_search({}, settings=s) == {}


@respx.mock
async def test_the_probe_raises_when_a_known_query_finds_nothing(s: Settings):
    """A query that must return something returning nothing means the search
    layer is broken, whatever it claims. Better to stop before a batch is
    drawn than to mark ten companies 'no website'."""
    respx.get(SEARXNG).mock(return_value=httpx.Response(200, json=searxng_body([])))
    with pytest.raises(SearchError):
        await probe_search({}, settings=s)


@respx.mock
async def test_the_probe_raises_when_search_is_down(s: Settings):
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SearchError):
        await probe_search({}, settings=s)


@respx.mock
async def test_the_probe_stops_on_a_rejected_brave_key(s: Settings):
    """Review focus 5. With a key set, every company search writes off is
    asked of Brave first; a rejected key would turn each of them into an
    error one by one. The probe finds out before a batch is drawn."""
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    respx.get(BRAVE).mock(return_value=httpx.Response(401))
    with pytest.raises(SearchError, match="BRAVE_SEARCH_API_KEY"):
        await probe_search({}, settings=keyed(s))


@respx.mock
async def test_the_probe_passes_when_both_providers_answer(s: Settings):
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    brave = respx.get(BRAVE).mock(
        return_value=httpx.Response(
            200, json=brave_body("https://example-register.ch/")
        )
    )
    assert await probe_search({}, settings=keyed(s)) == {}
    assert brave.called


@respx.mock
async def test_the_probe_stops_when_brave_finds_nothing(s: Settings):
    """A known query answered with nothing is a broken provider, whatever
    its status code says — the same rule as for SearXNG."""
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    respx.get(BRAVE).mock(return_value=httpx.Response(200, json=brave_body()))
    with pytest.raises(SearchError, match="Brave returned nothing"):
        await probe_search({}, settings=keyed(s))
