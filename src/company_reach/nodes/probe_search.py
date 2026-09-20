"""Pre-flight for the search layer: one known query must return something.

The point is the order. If search is broken, every company in the batch comes
back as "no website" — a finding, recorded as fact, that keeps ten good
candidates out of every future batch because `seen` remembers them and no
error was stored for a later run to recover from. `search.py` guards the case
where the engines report themselves unresponsive; this guards the case where
they quietly return nothing at all.

So the check runs before a single company is drawn, and it raises.
"""

from typing import Any

from company_reach.errors import SearchError
from company_reach.settings import Settings
from company_reach.tools.search import search

# Generic and stable: no company, no person, nothing that dates. If this
# returns nothing, the problem is ours, not the query's.
PROBE_QUERY = "handelsregister schweiz"


async def probe_search(state: dict[str, Any], *, settings: Settings) -> dict:
    results = await search(PROBE_QUERY, settings=settings, limit=3)
    if not results:
        raise SearchError(
            f"the probe query {PROBE_QUERY!r} returned nothing; search is not "
            "working, and every company would be recorded as having no website"
        )
    return {}
