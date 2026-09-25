"""A stand-in for search in node tests.

find_site asks `search_outcome`, which reports every provider it asked for
one query. Most tests only care what came back, so they write a plain
`search`-shaped fake — results, or a `SearchError` — and wrap it here, as if
SearXNG had answered."""

from company_reach.errors import SearchError
from company_reach.tools.search import Asked


def as_outcome(fake):
    async def outcome(query, *, settings, limit=10):
        try:
            results = await fake(query, settings=settings, limit=limit)
        except SearchError as error:
            return [Asked(query, "searxng", error=str(error))]
        return [Asked(query, "searxng", results)]

    return outcome
