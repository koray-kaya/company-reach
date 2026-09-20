"""The parent graph: draw a batch, fan out one child per company, collect,
decide whether to draw again.

The state below is the only thing the nodes share. It holds ids, counts and
small records; the pool, the pages and the results live in SQLite. That is
deliberate — a state that grows with the data would carry the whole canton
through every superstep.
"""

import operator
from typing import Annotated, TypedDict

from company_reach.models import CompanyResult, SelectionCriteria


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
