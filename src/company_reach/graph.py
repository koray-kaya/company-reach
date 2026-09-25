"""The parent graph: draw a batch, fan out one child per company, collect,
decide whether to draw again.

The state below is the only thing the nodes share. It holds ids, counts and
small records; the pool, the pages and the results live in SQLite. That is
deliberate — a state that grows with the data would carry the whole canton
through every superstep.
"""

import asyncio
import operator
from collections.abc import Callable
from functools import partial
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from company_reach.models import (
    CompanyProfile,
    CompanyRecord,
    CompanyResult,
    Contact,
    Draft,
    RawProfile,
    SelectionCriteria,
)
from company_reach.nodes.check_draft import check_draft
from company_reach.nodes.check_profile import check_profile
from company_reach.nodes.draft import draft
from company_reach.nodes.enrich_company import enrich_company
from company_reach.nodes.extract import extract
from company_reach.nodes.find_contact import find_contact
from company_reach.nodes.find_site import SiteChoice, find_site
from company_reach.nodes.load_company import load_company
from company_reach.nodes.pick_pages import pick_pages
from company_reach.nodes.probe_search import probe_search
from company_reach.nodes.read_pages import read_pages
from company_reach.nodes.recommend import recommend
from company_reach.profile import goal_hash, load_profile
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import (
    connect,
    count_sendable,
    current_criteria_hash,
    errored_uids,
    no_site_uids,
    record_pending,
    record_seen,
)
from company_reach.tools.db import draw_batch as db_draw_batch
from company_reach.tools.fetcher import Fetcher

# The child's longest path: load_company, find_site, pick_pages, read_pages,
# extract, check_profile, find_contact, recommend, draft, check_draft.
CHILD_LONGEST_PATH = 10


def recursion_limit(max_batches: int) -> int:
    """LangGraph's default limit is 1000; an unset limit is not a defence.
    This one follows the batch cap, so a larger cap cannot hit it.

    Three supersteps per batch (draw_batch, the enrich_company fan-out,
    collect), plus three: the search probe, and a last draw that finds the
    pool empty with its collect. The slack is the child's longest path: the
    child runs inside enrich_company and inherits this limit (measured), so
    a limit shorter than the child would fail every company."""
    return 3 * max_batches + 3 + CHILD_LONGEST_PATH


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
    target: int  # sendable candidates this run draws for

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
    """The loop's only exit decision. Three ways out: the run has the
    `target` number of sendable candidates, the pool ran dry, or the batch
    budget is spent. A target of one is the loop as M3 built it."""
    if (
        state["sendable_count"] < state["target"]
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
    goal = goal_hash(state["goal"])

    with connect(settings.db_path) as conn:
        uids = db_draw_batch(
            conn,
            run_id=state["run_id"],
            batch_no=batch_no,
            goal_hash=goal,
            prompt_version=prompt_version,
            model=settings.llm_model,
            criteria_hash=current_criteria_hash(conn, goal),
            min_score=settings.draw_min_score,
            limit=state["batch_size"],
        )
        if uids:
            record_seen(conn, uids, run_id=state["run_id"], batch_no=batch_no)
            record_pending(conn, uids, run_id=state["run_id"])

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


class ChildState(TypedDict, total=False):
    """`total=False` here, unlike the parent: the child fills its keys as it
    goes, and a company with no site never gets a `site` at all."""

    run_id: str
    uid: str
    goal: str
    about_me: str
    previous_error: str | None
    company: CompanyRecord | None
    site: SiteChoice | None
    page_urls: list[str]
    pages_to_read: list[str]
    page_texts: dict[str, str]
    needs_js: list[str]
    raw_profile: RawProfile | None
    profile: CompanyProfile | None
    contact: Contact | None
    contact_id: int | None
    recommendation: str | None
    reason: str | None
    draft: Draft | None
    draft_feedback: str


def has_site(state: ChildState) -> str:
    """The one branch the model decides. No site is a finding and the child
    is done; a site means there are pages to read."""
    return "pick_pages" if state.get("site") is not None else END


def is_send(state: ChildState) -> str:
    """Only a company worth writing to costs a drafting call; a skip or a
    hold ends here with its reason (`graph.spec.yaml:199`)."""
    return "draft" if state.get("recommendation") == "send" else END


def _stub_recommend(state: ChildState) -> dict:
    return {"recommendation": "skip", "reason": "the child graph arrives in M4"}


def build_stub_child():
    """The M3 child: one node, every company skipped. Kept because `run --dry`
    still uses it — that command's point is to exercise the loop without
    search, fetch or a model call."""
    builder = StateGraph(ChildState)
    builder.add_node("recommend", _stub_recommend)
    builder.add_edge(START, "recommend")
    builder.add_edge("recommend", END)
    return builder.compile()


Until = Literal["site", "profile", "contact", "draft"]


def build_child(
    *, settings: Settings, fetcher=None, until: Until = "draft"
) -> CompiledStateGraph:
    """The real child: register record, website, pages, profile, contact,
    recommendation, and for a company worth writing to, a checked draft.

    One `Fetcher` is threaded through rather than built per node, because the
    per-host delay and the robots cache live on the instance — a fetcher each
    would forget both between the nodes of one company's graph."""
    shared = fetcher or Fetcher(settings)
    builder = StateGraph(ChildState)
    builder.add_node("load_company", partial(load_company, settings=settings))
    builder.add_node("find_site", partial(find_site, settings=settings, fetcher=shared))

    builder.add_edge(START, "load_company")
    builder.add_edge("load_company", "find_site")

    # `until` builds a shorter graph rather than interrupting a longer one:
    # LangGraph's interrupts need a checkpointer, and this project has none
    # on purpose. `enrich --until site` is how M4 is looked at, and it should
    # keep costing what M4 cost — no page reads, no extraction.
    if until == "site":
        builder.add_edge("find_site", END)
        return builder.compile()

    builder.add_node("pick_pages", partial(pick_pages, settings=settings))
    builder.add_node(
        "read_pages", partial(read_pages, settings=settings, fetcher=shared)
    )
    builder.add_node("extract", partial(extract, settings=settings))
    builder.add_node("check_profile", partial(check_profile, settings=settings))

    builder.add_conditional_edges("find_site", has_site, ["pick_pages", END])
    builder.add_edge("pick_pages", "read_pages")
    builder.add_edge("read_pages", "extract")
    builder.add_edge("extract", "check_profile")
    if until == "profile":
        builder.add_edge("check_profile", END)
        return builder.compile()

    builder.add_node("find_contact", partial(find_contact, settings=settings))
    builder.add_node("recommend", recommend)
    builder.add_edge("check_profile", "find_contact")
    builder.add_edge("find_contact", "recommend")
    if until == "contact":
        builder.add_edge("recommend", END)
        return builder.compile()

    builder.add_node("draft", partial(draft, settings=settings))
    builder.add_node("check_draft", partial(check_draft, settings=settings))
    builder.add_conditional_edges("recommend", is_send, ["draft", END])
    builder.add_edge("draft", "check_draft")
    builder.add_edge("check_draft", END)
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
    target: int = 1,
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
        target=target,
        pool_count=0,
        kept_count=0,
        criteria=None,
        batch_uids=[],
        batches_drawn=0,
        pool_exhausted=False,
        results=[],
        sendable_count=0,
    )


def build_graph(*, settings: Settings, child, dry: bool = False) -> CompiledStateGraph:
    """The loop. Pool stages (`load_pool`, `screen_pool`, `write_criteria`,
    `score_pool`) stay CLI commands for now and fill the database this graph
    reads — that is what makes `run --dry` offline and fast.

    `dry` leaves `probe_search` out of the graph entirely. The probe exists to
    stop a run before search failures get recorded as "no website"; a run that
    never searches has nothing to protect, and wiring it in would put a
    network call inside the one command whose point is that it needs none.

    `functools.partial` binds `settings` and `child` ahead of time, because a
    LangGraph node is called with the state and nothing else.
    """
    builder = StateGraph(ReachState)
    if not dry:
        builder.add_node("probe_search", partial(probe_search, settings=settings))
    builder.add_node("draw_batch", partial(draw_batch, settings=settings))
    builder.add_node(
        "enrich_company", partial(enrich_company, child=child, settings=settings)
    )
    builder.add_node("collect", partial(collect, settings=settings))

    if dry:
        builder.add_edge(START, "draw_batch")
    else:
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


async def run_graph(
    state: ReachState,
    *,
    settings: Settings,
    child,
    dry: bool = False,
    on_result: Callable[[CompanyResult], None] | None = None,
) -> ReachState:
    """Streams rather than waiting for the end. "values" carries the whole
    state after each superstep, and the last one is what `ainvoke` would have
    returned; "updates" carries each node's output as it lands, which is how
    a finished company reaches `on_result` while its siblings still run.

    `recursion_limit` is passed here rather than left to the default of
    1000, and follows the batch cap (`recursion_limit` above)."""
    graph = build_graph(settings=settings, child=child, dry=dry)
    final = state
    # the graph traces every state it passes; only the setting may allow it
    with llm.tracing(settings):
        async for mode, chunk in graph.astream(
            state,
            config={"recursion_limit": recursion_limit(settings.max_batches_per_run)},
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                final = chunk
            elif on_result is not None:
                for node, update in chunk.items():
                    if node == "enrich_company" and update:
                        for result in update.get("results", []):
                            on_result(result)
    return final


async def retry_errors(
    run_id: str,
    *,
    settings: Settings,
    child,
    dry: bool = False,
    no_site: bool = False,
) -> list[CompanyResult]:
    """Re-enrich exactly the companies of `run_id` whose result is an error.

    The audit's P1 (`audit-2026-09-19.md:207`): an infrastructure failure
    must not consume a company. Nothing is drawn and `seen` is not touched —
    these companies were drawn by this run and stay drawn by it. Each goes
    through `enrich_company` again, which replaces its results row, so a
    recovered company leaves no error behind.

    `no_site` adds the companies written off as having no website (#20):
    finished rows, so they are forced through the child again.

    Search is probed first, as a run does: retrying while search is down
    would only record the same errors again.
    """
    with connect(settings.db_path) as conn:
        uids = errored_uids(conn, run_id)
        if no_site:
            uids += no_site_uids(conn, run_id)
    if not uids:
        return []
    if not dry:
        await probe_search({}, settings=settings)

    about_me = load_profile(settings.profile_path).about_me
    outs = await asyncio.gather(
        *(
            enrich_company(
                {"run_id": run_id, "uid": uid, "goal": "", "about_me": about_me},
                child=child,
                settings=settings,
                force=no_site,
            )
            for uid in uids
        )
    )
    return [out["results"][0] for out in outs]
