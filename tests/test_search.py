"""Search, and the one distinction the whole milestone rests on.

An empty result list is usually an answer: this company has no website. But
if every engine that could have answered was suspended, the same empty list
means nothing was asked. Recording the first as a finding is right; recording
the second as a finding is a lie about the company, and it burns a candidate
that will never be looked at again.
"""

import asyncio

import httpx
import pytest
import respx

from company_reach.errors import SearchError
from company_reach.nodes.probe_search import probe_search
from company_reach.settings import Settings
from company_reach.tools import search as search_module
from company_reach.tools.search import Result, search

SEARXNG = "http://searxng:8080/search"
SERPER = "https://google.serper.dev/search"


@pytest.fixture
def s(settings: Settings, monkeypatch) -> Settings:
    """No politeness sleeping in tests; the gap has its own test."""
    monkeypatch.setattr(search_module.asyncio, "sleep", _no_sleep)
    return settings


async def _no_sleep(seconds: float) -> None:
    return None


def searxng_body(results: list[dict], unresponsive: list | None = None) -> dict:
    return {"results": results, "unresponsive_engines": unresponsive or []}


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


# --- the Serper fallback -----------------------------------------------------


@respx.mock
async def test_serper_is_not_called_when_searxng_answers(s: Settings, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    s = Settings(_env_file=None, data_dir=s.data_dir)
    respx.get(SEARXNG).mock(
        return_value=httpx.Response(200, json=searxng_body([one_result()]))
    )
    route = respx.post(SERPER)
    await search("anything", settings=s)
    assert not route.called


@respx.mock
async def test_serper_is_not_called_for_an_empty_result(s: Settings, monkeypatch):
    """Falling back on empty results would spend paid queries on exactly the
    companies that have no website."""
    monkeypatch.setenv("SERPER_API_KEY", "k")
    s = Settings(_env_file=None, data_dir=s.data_dir)
    respx.get(SEARXNG).mock(return_value=httpx.Response(200, json=searxng_body([])))
    route = respx.post(SERPER)
    assert await search("anything", settings=s) == []
    assert not route.called


@respx.mock
async def test_serper_takes_over_after_a_search_error(s: Settings, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    s = Settings(_env_file=None, data_dir=s.data_dir)
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    respx.post(SERPER).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "link": "https://muster-metallbau.ch/",
                        "title": "Muster Metallbau AG",
                        "snippet": "Metallteile",
                    }
                ]
            },
        )
    )
    found = await search("anything", settings=s)
    assert found[0].url == "https://muster-metallbau.ch/"
    assert found[0].engine == "serper"


@respx.mock
async def test_without_a_key_the_search_error_stands(s: Settings):
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SearchError):
        await search("anything", settings=s)


@respx.mock
async def test_serper_failing_too_raises(s: Settings, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    s = Settings(_env_file=None, data_dir=s.data_dir)
    respx.get(SEARXNG).mock(side_effect=httpx.ConnectError("down"))
    respx.post(SERPER).mock(return_value=httpx.Response(429))
    with pytest.raises(SearchError):
        await search("anything", settings=s)


# --- rate control ------------------------------------------------------------


@respx.mock
async def test_at_most_two_queries_are_in_flight(s: Settings):
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
