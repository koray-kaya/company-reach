"""The parent graph: draw a batch, fan out one child per company, collect,
decide whether to draw again.

The state below is the only thing the nodes share. It holds ids, counts and
small records; the pool, the pages and the results live in SQLite. That is
deliberate — a state that grows with the data would carry the whole canton
through every superstep.
"""

import operator
from functools import partial
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from company_reach.models import CompanyResult, SelectionCriteria
from company_reach.nodes.enrich_company import enrich_company
from company_reach.nodes.probe_search import probe_search
from company_reach.profile import goal_hash
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import connect, count_sendable, record_seen
from company_reach.tools.db import draw_batch as db_draw_batch

RECURSION_LIMIT = 40


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


# --- the loop's own nodes ----------------------------------------------------


def draw_batch(state: ReachState, *, settings: Settings) -> dict:
    """Draw the next batch and record it as seen.

    The batch number is derived rather than stored, so a resumed run asks for
    the same batch it asked for before and `db.draw_batch` hands back what it
    recorded. `batches_drawn` only advances when a batch actually came back:
    it counts batches drawn, not attempts.
    """
    batch_no = state["batches_drawn"] + 1
    prompt_version, _ = llm.load_prompt("score")

    with connect(settings.db_path) as conn:
        uids = db_draw_batch(
            conn,
            run_id=state["run_id"],
            batch_no=batch_no,
            goal_hash=goal_hash(state["goal"]),
            prompt_version=prompt_version,
            model=settings.llm_model,
            min_score=settings.draw_min_score,
            limit=state["batch_size"],
        )
        if uids:
            record_seen(conn, uids, run_id=state["run_id"], batch_no=batch_no)

    return {
        "batch_uids": uids,
        "batches_drawn": batch_no if uids else state["batches_drawn"],
        "pool_exhausted": not uids,
    }


def fan_out(state: ReachState) -> list[Send] | str:
    """One `Send` per company: LangGraph runs them as separate tasks in one
    superstep, each with its own input, and the `results` reducer joins what
    they return. An empty batch routes straight to `collect` — returning an
    empty list of Sends would leave the graph with nowhere to go.
    """
    if not state["batch_uids"]:
        return "collect"
    return [
        Send(
            "enrich_company",
            {
                "run_id": state["run_id"],
                "uid": uid,
                "goal": state["goal"],
                "about_me": state["about_me"],
            },
        )
        for uid in state["batch_uids"]
    ]


# --- the child, stubbed until M4 ---------------------------------------------


class ChildState(TypedDict):
    run_id: str
    uid: str
    goal: str
    about_me: str
    recommendation: str | None
    reason: str | None


def _stub_recommend(state: ChildState) -> dict:
    return {"recommendation": "skip", "reason": "the child graph arrives in M4"}


def build_stub_child():
    """A child graph with one node. M4 gives it find_site, pick_pages and the
    rest; nothing above it changes, because the parent only ever awaits
    `ainvoke` and reads `recommendation`."""
    builder = StateGraph(ChildState)
    builder.add_node("recommend", _stub_recommend)
    builder.add_edge(START, "recommend")
    builder.add_edge("recommend", END)
    return builder.compile()


# --- assembly ----------------------------------------------------------------


def initial_state(
    *,
    run_id: str,
    goal: str,
    about_me: str,
    municipality: str,
    settings: Settings,
    seed: int = 0,
) -> ReachState:
    """Every key present from the start. A TypedDict without `total=False`
    promises that, and LangGraph reads keys the first node never wrote."""
    return ReachState(
        run_id=run_id,
        goal=goal,
        about_me=about_me,
        municipality=municipality,
        batch_size=settings.batch_size,
        seed=seed,
        pool_count=0,
        kept_count=0,
        criteria=None,
        batch_uids=[],
        batches_drawn=0,
        pool_exhausted=False,
        results=[],
        sendable_count=0,
    )


def build_graph(*, settings: Settings, child) -> CompiledStateGraph:
    """The loop. Pool stages (`load_pool`, `screen_pool`, `write_criteria`,
    `score_pool`) stay CLI commands for now and fill the database this graph
    reads — that is what makes `run --dry` offline and fast.

    `functools.partial` binds `settings` and `child` ahead of time, because a
    LangGraph node is called with the state and nothing else.
    """
    builder = StateGraph(ReachState)
    builder.add_node("probe_search", partial(probe_search, settings=settings))
    builder.add_node("draw_batch", partial(draw_batch, settings=settings))
    builder.add_node(
        "enrich_company", partial(enrich_company, child=child, settings=settings)
    )
    builder.add_node("collect", partial(collect, settings=settings))

    builder.add_edge(START, "probe_search")
    builder.add_edge("probe_search", "draw_batch")
    builder.add_conditional_edges("draw_batch", fan_out, ["enrich_company", "collect"])
    builder.add_edge("enrich_company", "collect")
    builder.add_conditional_edges(
        "collect",
        partial(need_another_batch, settings=settings),
        ["draw_batch", END],
    )
    return builder.compile()


async def run_graph(state: ReachState, *, settings: Settings, child) -> ReachState:
    """`recursion_limit` is passed here rather than left to the default of
    1000: an unset limit is not a defence, and a routing bug should fail in
    forty supersteps, not a thousand."""
    graph = build_graph(settings=settings, child=child)
    return await graph.ainvoke(state, config={"recursion_limit": RECURSION_LIMIT})
