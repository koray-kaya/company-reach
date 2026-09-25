"""The wrapper node: one company, one child graph, one result row.

This is the audit's first P0 fix, and it is why M3 exists as a milestone.

A compiled subgraph can be added to a parent graph as a node directly, and it
looks like it works. It does not. LangGraph's `StateGraph._get_updates` keeps
only the keys the child's output shares with the parent's schema — the child
has no `results` key, so everything it produced is filtered out, silently and
without an error. `sendable_count` then stays 0 for ever: the parent draws
three batches and reports nothing.

So the child is invoked from a plain function instead, and that function
writes `{"results": [...]}` itself. The same function gives us the error rule
for free: the child's nodes raise, and this is the one place that catches.
That matters beyond tidiness — LangGraph cancels the sibling `Send` tasks of
a superstep when one task raises, so "one failure does not stop the others"
is true only as long as this function never raises.
"""

from typing import Any

from company_reach.errors import FetchError, LlmError, SearchError, ShabError
from company_reach.models import CompanyResult, ErrorKind
from company_reach.settings import Settings
from company_reach.tools.db import (
    closed_because,
    connect,
    record_result,
    result_for,
)

_ERROR_KINDS: list[tuple[type[Exception], ErrorKind]] = [
    (SearchError, "search"),
    (FetchError, "fetch"),
    (LlmError, "llm"),
    (ShabError, "shab"),
]

_MAX_ERROR_TEXT = 500


def _kind(error: Exception) -> ErrorKind:
    for error_type, kind in _ERROR_KINDS:
        if isinstance(error, error_type):
            return kind
    return "other"


async def enrich_company(
    state: dict[str, Any], *, child: Any, settings: Settings, force: bool = False
) -> dict[str, list[CompanyResult]]:
    """Run the child graph for one company and return its result.

    `child` is passed in rather than imported so that the graph module can
    wire a stub today and the real child in M4, and so a test can hand it a
    double. The graph binds it with `functools.partial`, because a LangGraph
    node is called with the state alone.

    `force` runs a company that already finished. Only `retry --no-site`
    uses it, for a "no website" that a throttled search may have produced.
    """
    run_id, uid = state["run_id"], state["uid"]

    with connect(settings.db_path) as conn:
        done = result_for(conn, uid, run_id=run_id)
        closed = closed_because(conn, uid)
    if done is not None and done.error_kind is None and not force:
        # Resume: this company is finished. Skip the child, but still return
        # the stored result — the superstep's accounting has to stay whole.
        return {"results": [done]}
    if closed:
        # A resumed batch whose card was marked Never, or forgotten, since
        # the draw: finished here, and nothing more collected (audit H7)
        result = CompanyResult(
            uid=uid, recommendation="skip", reason=f"not enriched: {closed}"
        )
        with connect(settings.db_path) as conn:
            record_result(conn, result, run_id=run_id)
        return {"results": [result]}

    try:
        out = await child.ainvoke(
            {
                "run_id": run_id,
                "uid": uid,
                "goal": state["goal"],
                "about_me": state["about_me"],
                # how the last attempt failed, so a refusal that repeats can
                # be told from one that happened once (find_site)
                "previous_error": done.error_text if done and done.error_kind else None,
            }
        )
        result = CompanyResult(
            uid=uid,
            recommendation=out.get("recommendation"),
            reason=out.get("reason"),
        )
    except Exception as error:  # noqa: BLE001 — nothing may escape this node
        result = CompanyResult(
            uid=uid,
            error_kind=_kind(error),
            error_text=str(error)[:_MAX_ERROR_TEXT],
        )

    with connect(settings.db_path) as conn:
        record_result(conn, result, run_id=run_id)
    return {"results": [result]}
