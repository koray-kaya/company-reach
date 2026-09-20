"""The parent graph: draw a batch, fan out one child per company, collect,
decide whether to draw again.

The state below is the only thing the nodes share. It holds ids, counts and
small records; the pool, the pages and the results live in SQLite. That is
deliberate — a state that grows with the data would carry the whole canton
through every superstep.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END

from company_reach.models import CompanyResult, SelectionCriteria
from company_reach.settings import Settings
from company_reach.tools.db import connect, count_sendable


class ReachState(TypedDict):
    """`total=False` is not used: every key is written before it is read, and
    a missing key should fail loudly rather than default to something.

    `results` is the one key with a reducer. Ten `enrich_company` nodes run in
    the same superstep and all write it; LangGraph treats concurrent writes to
    a plain key as a conflict, so the key declares how to combine them.
    `operator.add` on two lists concatenates, giving ten entries.
    """

    run_id: str
    goal: str
    about_me: str
    municipality: str
    batch_size: int
    seed: int

    pool_count: int
    kept_count: int
    criteria: SelectionCriteria | None

    batch_uids: list[str]  # replaced each time round the loop
    batches_drawn: int
    pool_exhausted: bool

    results: Annotated[list[CompanyResult], operator.add]
    sendable_count: int


def collect(state: ReachState, *, settings: Settings) -> dict:
    """How many candidates this run has ready for review.

    From the `results` table, never from `state["results"]`. This is the
    audit's third P0 finding: on a rerun of the same run_id every child is
    skipped, so state carries an empty list while the table is full. Counting
    state would report zero, the router would ask for another batch, and the
    resume rule would be broken by the very code meant to honour it.
    """
    with connect(settings.db_path) as conn:
        return {"sendable_count": count_sendable(conn, state["run_id"])}


def need_another_batch(state: ReachState, *, settings: Settings) -> str:
    """The loop's only exit decision. Three ways out: a candidate was found,
    the pool ran dry, or the batch budget is spent."""
    if (
        state["sendable_count"] == 0
        and state["batches_drawn"] < settings.max_batches_per_run
        and not state["pool_exhausted"]
    ):
        return "draw_batch"
    return END
