import operator
from typing import get_type_hints

from company_reach.graph import ReachState
from company_reach.models import CompanyResult


def test_results_carries_an_add_reducer():
    """Several enrich_company nodes write `results` in the same superstep.
    Without a reducer LangGraph treats concurrent writes to one key as a
    conflict; operator.add concatenates the lists instead."""
    hints = get_type_hints(ReachState, include_extras=True)
    assert hints["results"].__metadata__ == (operator.add,)


def test_results_holds_company_results():
    hints = get_type_hints(ReachState, include_extras=True)
    annotated_type = hints["results"].__origin__
    assert annotated_type == list[CompanyResult]


def test_state_carries_the_loop_controls():
    hints = get_type_hints(ReachState, include_extras=True)
    for key in ("batches_drawn", "pool_exhausted", "sendable_count", "batch_uids"):
        assert key in hints
