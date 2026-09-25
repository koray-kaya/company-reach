"""Web search, and the distinction the milestone rests on.

An empty result list is normally an answer: this company has no findable
website. Five of the twenty companies in the earlier prototype were like
that, and that is a fact about the population, not a failure.

But the same empty list means something else entirely when every engine that
could have answered was suspended or timed out. Recording *that* as "no
website" would be a claim with no evidence behind it, and it would burn the
candidate — `seen` keeps it out of the next batch, and nothing carries an
error for a later run to recover from.

So: the engines worked and found nothing → `[]`. Nothing was asked →
`SearchError`, which the wrapper turns into an error result, which a later
run retries.

Two providers answer. SearXNG, self-hosted and free, is always asked first.
The Brave Search API is paid and optional: `search` asks it only when
SearXNG could not answer a query, and find_site asks it in the two further
cases it decides (silence, and before any "no website"). `search_outcome`
reports every provider asked, with SearXNG's unresponsive engines and any
error, because a "no website" must be able to show what it rests on (#20).
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal

import httpx

from company_reach.errors import SearchError
from company_reach.settings import Settings
from company_reach.tools.gates import gate

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
# Brave's own ceiling for one request.
_BRAVE_MAX_COUNT = 20


@dataclass(frozen=True)
class Result:
    """`engine` is what answered inside a provider; `provider` is whom we
    asked. They are kept apart because SearXNG has an engine called "brave"
    of its own, and a SearXNG result from it is free to store while a result
    from the Brave Search API is not (its terms)."""

    url: str
    title: str
    snippet: str
    engine: str
    provider: Literal["searxng", "brave", "guess"] = "searxng"


@dataclass(frozen=True)
class Asked:
    """One query put to one provider, and what came back. `error` set means
    the provider could not answer, and `results` is then empty; an empty
    `results` without an error is an answer."""

    query: str
    provider: Literal["searxng", "brave"]
    results: list[Result] = field(default_factory=list)
    unresponsive: list[str] = field(default_factory=list)
    error: str | None = None


def _gate(concurrency: int) -> asyncio.Semaphore:
    """Search's cap for the running event loop (`tools/gates.py`), kept apart
    from the model's."""
    return gate("search", concurrency)


def _unresponsive(payload: dict) -> set[str]:
    """SearXNG reports these as `[name, reason]` pairs in current versions and
    as bare names in older ones. Accept both rather than pin a version."""
    names: set[str] = set()
    for entry in payload.get("unresponsive_engines") or []:
        if isinstance(entry, str):
            names.add(entry.lower())
        elif entry:
            names.add(str(entry[0]).lower())
    return names


async def _searxng(query: str, *, settings: Settings, limit: int) -> Asked:
    """One SearXNG request. Failures come back inside the `Asked` rather than
    raised, so the engines that did not answer are kept either way."""
    url = f"{settings.searxng_url.rstrip('/')}/search"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            answer = await client.get(
                url,
                params={
                    "q": query,
                    "format": "json",
                    "language": "de-CH",
                    "safesearch": 0,
                    "pageno": 1,
                },
            )
    except httpx.HTTPError as error:
        return Asked(query, "searxng", error=f"SearXNG unreachable: {error}")

    if answer.status_code != 200:
        return Asked(
            query, "searxng", error=f"SearXNG answered HTTP {answer.status_code}"
        )

    payload = answer.json()
    results = payload.get("results") or []
    down = sorted(_unresponsive(payload))

    if not results:
        baseline = {
            name.strip().lower()
            for name in settings.baseline_engines.split(",")
            if name.strip()
        }
        if baseline and baseline <= set(down):
            return Asked(
                query,
                "searxng",
                unresponsive=down,
                error="every baseline engine was unresponsive "
                f"({', '.join(sorted(baseline))}); the empty result is not an answer",
            )

    return Asked(
        query,
        "searxng",
        [
            Result(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                engine=item.get("engine", ""),
            )
            for item in results[:limit]
            if item.get("url")
        ],
        unresponsive=down,
    )


async def ask_searxng(query: str, *, settings: Settings, limit: int = 10) -> Asked:
    """One SearXNG query, through the gate."""
    async with _gate(settings.search_concurrency):
        asked = await _searxng(query, settings=settings, limit=limit)
        # Inside the gate: the gap is between queries leaving, not between
        # callers arriving, or two waiting tasks would fire back to back.
        await asyncio.sleep(settings.search_gap_s)
        return asked


async def _brave(query: str, *, settings: Settings, limit: int) -> list[Result]:
    """The Brave Search API, the paid second opinion.

    Every answer but a 200 is an error. An earlier project that used Brave
    let a 5xx come back as `[]`, and its quality dropped for weeks before
    anyone noticed: an empty list must only ever mean that Brave looked and
    found nothing. Brave has its own gate, so waiting SearXNG queries never
    hold up the provider that is asked when SearXNG is in trouble."""
    key = settings.brave_search_api_key
    if key is None:
        raise SearchError("Brave is not configured: BRAVE_SEARCH_API_KEY is not set")
    async with gate("brave", 2):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                answer = await client.get(
                    _BRAVE_URL,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": key.get_secret_value(),
                    },
                    params={
                        "q": query,
                        "count": min(limit, _BRAVE_MAX_COUNT),
                        "country": "CH",
                        "search_lang": "de",
                    },
                )
        except httpx.HTTPError as error:
            raise SearchError(f"Brave unreachable: {error}") from error

    if answer.status_code in (401, 403):
        raise SearchError(
            f"Brave rejected the key (HTTP {answer.status_code}); "
            "check BRAVE_SEARCH_API_KEY"
        )
    if answer.status_code != 200:
        raise SearchError(f"Brave answered HTTP {answer.status_code}")

    items = (answer.json().get("web") or {}).get("results") or []
    return [
        Result(
            url=item.get("url", ""),
            title=item.get("title", ""),
            snippet=item.get("description", ""),
            engine="brave-api",
            provider="brave",
        )
        for item in items[:limit]
        if item.get("url")
    ]


async def ask_brave(query: str, *, settings: Settings, limit: int = 10) -> Asked:
    """`_brave`, with its failure kept as a value like SearXNG's."""
    try:
        results = await _brave(query, settings=settings, limit=limit)
    except SearchError as error:
        return Asked(query, "brave", error=str(error))
    return Asked(query, "brave", results)


async def search_outcome(
    query: str, *, settings: Settings, limit: int = 10
) -> list[Asked]:
    """Every provider asked for one query, in order: SearXNG, then Brave
    **only** when SearXNG could not answer and a key is set. Never after an
    empty result: that would spend paid queries on precisely the companies
    that have no website — the ones where the free search was already
    right."""
    asked = [await ask_searxng(query, settings=settings, limit=limit)]
    if asked[0].error is not None and settings.brave_search_api_key is not None:
        asked.append(await ask_brave(query, settings=settings, limit=limit))
    return asked


def results_of(asked: list[Asked]) -> list[Result]:
    """The answer the last provider gave, or a `SearchError` naming every
    provider's failure when none could answer."""
    if asked[-1].error is not None:
        raise SearchError("; ".join(a.error for a in asked if a.error))
    return asked[-1].results


async def search(query: str, *, settings: Settings, limit: int = 10) -> list[Result]:
    """One query's results, for a caller that does not keep a search log."""
    return results_of(await search_outcome(query, settings=settings, limit=limit))
