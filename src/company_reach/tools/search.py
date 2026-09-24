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
"""

import asyncio
from dataclasses import dataclass

import httpx

from company_reach.errors import SearchError
from company_reach.settings import Settings
from company_reach.tools.gates import gate

_SERPER_URL = "https://google.serper.dev/search"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


@dataclass(frozen=True)
class Result:
    url: str
    title: str
    snippet: str
    engine: str


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


async def _searxng(query: str, *, settings: Settings, limit: int) -> list[Result]:
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
        raise SearchError(f"SearXNG unreachable: {error}") from error

    if answer.status_code != 200:
        raise SearchError(f"SearXNG answered HTTP {answer.status_code}")

    payload = answer.json()
    results = payload.get("results") or []

    if not results:
        baseline = {
            name.strip().lower()
            for name in settings.baseline_engines.split(",")
            if name.strip()
        }
        if baseline and baseline <= _unresponsive(payload):
            raise SearchError(
                "every baseline engine was unresponsive "
                f"({', '.join(sorted(baseline))}); the empty result is not an answer"
            )

    return [
        Result(
            url=item.get("url", ""),
            title=item.get("title", ""),
            snippet=item.get("content", ""),
            engine=item.get("engine", ""),
        )
        for item in results[:limit]
        if item.get("url")
    ]


async def _serper(query: str, *, settings: Settings, limit: int) -> list[Result]:
    key = settings.serper_api_key
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            answer = await client.post(
                _SERPER_URL,
                headers={"X-API-KEY": key.get_secret_value()},
                json={"q": query, "gl": "ch", "hl": "de", "num": limit},
            )
    except httpx.HTTPError as error:
        raise SearchError(f"Serper unreachable: {error}") from error

    if answer.status_code != 200:
        raise SearchError(f"Serper answered HTTP {answer.status_code}")

    return [
        Result(
            url=item.get("link", ""),
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
            engine="serper",
        )
        for item in (answer.json().get("organic") or [])[:limit]
        if item.get("link")
    ]


async def search(query: str, *, settings: Settings, limit: int = 10) -> list[Result]:
    """One query, through the gate.

    Serper is tried **only** after a `SearchError`, never after an empty
    result. Falling back on empty would spend paid queries on precisely the
    companies that have no website — the ones where the free search was
    already right.
    """
    async with _gate(settings.search_concurrency):
        try:
            results = await _searxng(query, settings=settings, limit=limit)
        except SearchError:
            if settings.serper_api_key is None:
                raise
            results = await _serper(query, settings=settings, limit=limit)
        # Inside the gate: the gap is between queries leaving, not between
        # callers arriving, or two waiting tasks would fire back to back.
        await asyncio.sleep(settings.search_gap_s)
        return results
