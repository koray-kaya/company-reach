"""Pre-flight for the search provider: one known query must return at least
one result, raised before a single company is drawn.

A stub until M4. It exists now so the edge into `draw_batch` is real and M4
replaces a body rather than rewiring the graph. It returns no state updates —
a node may do that; LangGraph simply writes nothing.
"""

from typing import Any

from company_reach.settings import Settings


def probe_search(state: dict[str, Any], *, settings: Settings) -> dict:
    return {}
