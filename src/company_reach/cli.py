"""Command line. typer turns each function's signature into a command with
--help; the wiring to the `company-reach` executable is [project.scripts]."""

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from company_reach.errors import CompanyReachError
from company_reach.graph import (
    build_child,
    build_stub_child,
    initial_state,
    retry_errors,
    run_graph,
)
from company_reach.import_v0 import import_v0
from company_reach.manifest import finish_manifest, manifest_path, start_manifest
from company_reach.models import CompanyResult, StoredCriteria
from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.score_pool import score_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.nodes.write_criteria import (
    criteria_hash,
    format_criteria,
    write_criteria,
)
from company_reach.profile import goal_hash, load_profile
from company_reach.settings import get_settings
from company_reach.tools import llm
from company_reach.tools.db import (
    connect,
    count_brave_queries,
    current_criteria_hash,
    errored_uids,
    init_db,
    load_criteria,
    pool_standing,
    record_run,
    store_criteria,
)
from company_reach.tools.doctor import run_checks

V0_DIR = Path("data/v0")

app = typer.Typer(help="Find Swiss companies, find the person, draft the mail.")


def _resolve_goal(explicit: str | None) -> str:
    """--goal wins; otherwise profile.toml. Trying a goal on the command line
    without editing the file is the common case while wording is still being
    worked out."""
    if explicit:
        return explicit.strip()
    return load_profile(get_settings().profile_path).goal


def _run_id(explicit: str | None) -> str:
    return explicit or f"r{uuid.uuid4().hex[:8]}"


def _require_a_scored_pool(s, goal: str, run_id: str) -> None:
    """Stop before a run that can draw nothing, and say why.

    A run that draws nothing prints a row of zeros and "pool exhausted",
    which reads as "this town is done". The guard asks what `draw_batch`
    asks, through the same SQL; it used to count any score for the goal, so
    a prompt bump or a pool below the bar passed it. A resumed run passes:
    it repeats the batches it drew."""
    version, _ = llm.load_prompt("score")
    key = goal_hash(goal)
    with connect(s.db_path) as conn:
        if conn.execute("select 1 from seen where run_id = ?", (run_id,)).fetchone():
            return
        standing = pool_standing(
            conn,
            run_id=run_id,
            goal_hash=key,
            prompt_version=version,
            model=s.llm_model,
            criteria_hash=current_criteria_hash(conn, key),
            min_score=s.draw_min_score,
        )
    if standing.drawable:
        return
    if standing.pooled == 0:
        typer.echo(
            "No company is pooled yet. Run `pool`, then `score` before `run`.",
            err=True,
        )
        raise typer.Exit(2)

    bar = f"score >= {s.draw_min_score} for score@{version} / {s.llm_model}"
    if standing.clear:
        head = (
            f"everything that clears {bar} ({standing.clear}) was drawn, "
            "decided or suppressed already: the pool is exhausted"
        )
    else:
        head = f"0 companies clear {bar}"
    reasons = []
    if standing.current and not standing.clear:
        reasons.append(f"{standing.current} are scored, the best {standing.best}")
    if standing.earlier_criteria:
        reasons.append(
            f"{standing.earlier_criteria} are scored under earlier criteria"
            " — run `score`"
        )
    if standing.other_prompt:
        versions = ", ".join(f"score@{v}" for v in standing.other_prompt_versions)
        reasons.append(
            f"{standing.other_prompt} are scored under another prompt version "
            f"({versions}) — run `score`"
        )
    if standing.other_model:
        reasons.append(
            f"{standing.other_model} are scored with another model — run `score`"
        )
    if standing.unscored:
        reasons.append(
            f"{standing.unscored} kept companies are not scored yet — run `score`"
        )
    if not (standing.earlier_criteria or standing.other_prompt or standing.unscored):
        reasons.append("pool another municipality to go on")
    typer.echo("; ".join([head, *reasons]), err=True)
    raise typer.Exit(2)


@app.command()
def doctor() -> None:
    """Check settings, prompts, database and model endpoint before a run."""
    checks = asyncio.run(run_checks(get_settings()))
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        typer.echo(f"{mark}  {check.name:<13} {check.detail}")
    if not all(check.ok for check in checks):
        raise typer.Exit(1)


NewCriteria = Annotated[
    bool,
    typer.Option(
        "--new-criteria",
        help="Write a fresh set of criteria for the goal. Scores made under "
        "the old set stop counting, so `score` scores the pool again.",
    ),
]


def _goal_criteria(s, goal: str, *, new: bool) -> StoredCriteria:
    """The goal's one set of criteria (audit H10): the stored set, or, the
    first time or with --new-criteria, a freshly written set that is stored
    before anything is scored against it."""
    key = goal_hash(goal)
    with connect(s.db_path) as conn:
        stored = None if new else load_criteria(conn, key)
    if stored is not None:
        typer.echo(
            f"criteria {stored.criteria_hash} · stored {stored.created_at} · "
            "`--new-criteria` writes a fresh set"
        )
        return stored

    written, prov = asyncio.run(write_criteria(goal, settings=s))
    with connect(s.db_path) as conn:
        adopted = store_criteria(
            conn,
            key,
            written,
            criteria_hash=criteria_hash(written),
            model=prov.model,
            prompt_version=prov.prompt_version,
            adopt_unlinked=not new,
        )
        stored = load_criteria(conn, key)
    typer.echo(
        f"criteria {stored.criteria_hash} · written now by {prov.model} · "
        f"{prov.prompt}@{prov.prompt_version} · {prov.seconds:.1f}s · stored"
    )
    if adopted:
        typer.echo(f"{adopted} scores made before criteria were stored count under it")
    if new:
        typer.echo("scores made under earlier criteria no longer count; run `score`")
    return stored


@app.command()
def criteria(goal: str | None = None, new_criteria: NewCriteria = False) -> None:
    """Show the goal's selection criteria before scoring anything. The first
    time they are written and stored; every `score` then uses that set."""
    s = get_settings()
    text = _resolve_goal(goal)
    typer.echo(f"goal     {text}")
    typer.echo(f"hash     {goal_hash(text)}")
    stored = _goal_criteria(s, text, new=new_criteria)
    typer.echo("")
    typer.echo(format_criteria(stored.criteria))


@app.command()
def score(
    goal: str | None = None,
    limit: int | None = None,
    seed: int = 0,
    run_id: str | None = None,
    new_criteria: NewCriteria = False,
) -> None:
    """Score screened companies against the goal. Incremental and resumable:
    already-scored companies cost nothing, so run it again to score more."""
    s = get_settings()
    text = _resolve_goal(goal)
    rid = _run_id(run_id)

    stored = _goal_criteria(s, text, new=new_criteria)
    typer.echo(format_criteria(stored.criteria))
    typer.echo("")

    record_run(s.db_path, rid, text, stored, seed=seed)
    report = asyncio.run(
        score_pool(rid, text, stored.criteria, settings=s, limit=limit, seed=seed)
    )
    typer.echo(
        f"{report.scored} newly scored · {report.cached} already scored, skipped"
        f" · {report.dropped} answers dropped · {report.failed_batches} batches "
        f"failed · {report.seconds:.0f}s · run {rid}"
    )
    typer.echo(
        "run `score --limit N` again to score N more; "
        "nothing already scored for this goal is asked twice."
    )


@app.command()
def pool(municipality: str = "3203", run_id: str | None = None) -> None:
    """Fetch a municipality's AG+GmbH companies from LINDAS into the database."""
    s = get_settings()
    init_db(s.db_path)
    rid = _run_id(run_id)
    n = load_pool(municipality, rid, settings=s)
    typer.echo(f"{n} companies stored for municipality {municipality} (run {rid})")


@app.command()
def screen(
    # Hidden and ignored: kept so a command written down before every import
    # was screened still runs, and says what changed instead of failing.
    run_id: Annotated[str | None, typer.Option(hidden=True)] = None,
) -> None:
    """Apply the rule-based exclusions again to every company. `pool`
    already screens what it stores; this is for when the rules change."""
    s = get_settings()
    if run_id is not None:
        typer.echo(
            "--run-id is no longer needed: every import is screened as it is "
            "stored, and `screen` covers every company."
        )
    kept, dropped = screen_pool(settings=s)
    if kept + dropped == 0:
        typer.echo("No company in the database to screen; run `pool` first.", err=True)
        raise typer.Exit(1)
    typer.echo(f"kept {kept}, dropped {dropped}")


@app.command()
def run(
    dry: Annotated[
        bool,
        typer.Option(
            "--dry",
            help="Loop over the stub child: no search, no fetch, no model call.",
        ),
    ] = False,
    goal: str | None = None,
    seed: int = 0,
    run_id: str | None = None,
    target: Annotated[
        int,
        typer.Option(
            min=1,
            help="Draw batches until this many companies are sendable, the "
            "pool runs dry, or MAX_BATCHES_PER_RUN batches are drawn.",
        ),
    ] = 1,
) -> None:
    """Draw batches of the best-scoring companies and work through them.

    The pool stages are separate commands, so this starts from a database that
    `pool` and `score` have already filled. That is what keeps `--dry` offline
    and quick enough to demonstrate.
    """
    s = get_settings()
    text = _resolve_goal(goal)
    rid = _run_id(run_id)
    _require_a_scored_pool(s, text, rid)

    with connect(s.db_path) as conn:
        stored = load_criteria(conn, goal_hash(text))
    start_manifest(rid, settings=s, goal=text, seed=seed, criteria=stored)
    state = initial_state(
        run_id=rid,
        goal=text,
        # --goal overrides the goal only; the drafts still say who writes
        about_me=load_profile(s.profile_path).about_me,
        municipality="",
        settings=s,
        seed=seed,
        target=target,
    )
    resume = _resume(rid, dry, goal, seed, target)

    typer.echo(f"run {rid} — if it stops, run it again with: {resume}")
    try:
        child = build_stub_child() if dry else build_child(settings=s)
        out = asyncio.run(
            run_graph(state, settings=s, child=child, dry=dry, on_result=_echo_result)
        )
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        finish_manifest(rid, settings=s, status="failed", counts={}, reason=reason)
        typer.echo(f"run {rid} failed — {reason}", err=True)
        raise typer.Exit(1) from error

    with connect(s.db_path) as conn:
        # From the table, not this call's children: a company an earlier,
        # crashed attempt left unfinished belongs in the count too.
        errors = len(errored_uids(conn, rid))
        brave = count_brave_queries(conn, rid)
    counts = {
        "batches_drawn": out["batches_drawn"],
        "results": len(out["results"]),
        "sendable": out["sendable_count"],
        "errors": errors,
        "brave_queries": brave,
    }
    finish_manifest(rid, settings=s, status="done", counts=counts)

    wanted = f" of {target}" if target > 1 else ""
    typer.echo(
        f"{counts['batches_drawn']} batches · {counts['results']} companies · "
        f"{counts['errors']} errors · {counts['sendable']}{wanted} sendable · "
        f"{brave} Brave queries"
        + ("  · pool exhausted" if out["pool_exhausted"] else "")
    )
    if (
        counts["sendable"] < target
        and not out["pool_exhausted"]
        and counts["batches_drawn"] >= s.max_batches_per_run
    ):
        typer.echo(
            f"stopped at the batch cap ({s.max_batches_per_run}); run again, "
            "or raise MAX_BATCHES_PER_RUN"
        )
    typer.echo(f"manifest: {manifest_path(rid, settings=s)}")
    if counts["errors"] and dry:
        # `retry` runs the real child; a dry run is finished by itself
        typer.echo(f"finish them with: {resume}")
    elif counts["errors"]:
        typer.echo(f"retry the errors with: company-reach retry {rid}")
    if dry:
        typer.echo("--dry: every company was skipped by the M3 stub child.")


@app.command("import-v0")
def import_v0_command(
    v0_dir: Annotated[
        Path, typer.Option(help="The v0 prototype's data: seen.json and outreach.md.")
    ] = V0_DIR,
) -> None:
    """Bring the v0 prototype's companies into seen and its skips into the
    ledger, so none of them is drawn again. Safe to run twice."""
    s = get_settings()
    init_db(s.db_path)
    with connect(s.db_path) as conn:
        seen, skipped = import_v0(conn, v0_dir)
    typer.echo(
        f"{seen} companies marked as seen · {skipped} "
        f"{'skip' if skipped == 1 else 'skips'} recorded"
    )


@app.command()
def review(
    run_id: Annotated[
        str | None, typer.Argument(help="The run to open; omit to pick one by URL.")
    ] = None,
    host: Annotated[
        str, typer.Option(help="127.0.0.1 outside Docker; the container uses 0.0.0.0.")
    ] = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve the review page: one company per screen, Send / Skip / Never.

    The tool never sends. Send records the decision and opens your own mail
    client with the draft; the mail leaves when you press send there.
    """
    from company_reach.review.app import create_app

    s = get_settings()
    path = f"/review/{run_id}" if run_id else "/review/<run_id>"
    typer.echo(f"review page: http://127.0.0.1:{port}{path}")
    if not s.sending_approved:
        typer.echo("Send is locked: SENDING_APPROVED is not set (ethics approval).")
    uvicorn.run(create_app(s), host=host, port=port)


@app.command()
def forget(
    key: Annotated[str, typer.Argument(help="A company's UID or a person's address.")],
) -> None:
    """Honour a deletion request: remove the person from the database and the
    page cache, and never contact the company or address again."""
    from company_reach.forget import forget as forget_key

    report = forget_key(get_settings(), key)
    typer.echo(
        f"{len(report.companies)} companies · {report.rows_deleted} rows and "
        f"{report.cache_files_deleted} cache files deleted"
    )
    typer.echo(f"suppressed: {', '.join(report.suppressed)}")
    for path in report.still_named:
        typer.echo(f"still named in {path} — a hand-kept file; edit it by hand")


@app.command()
def purge(
    older_than: Annotated[
        int, typer.Option(help="Days since a company was drawn or last decided.")
    ] = 365,
) -> None:
    """Remove personal data nobody has touched for a while: pages, cache,
    profiles, contacts, drafts. The ledger and the never-again list stay."""
    from company_reach.forget import purge as purge_old

    report = purge_old(get_settings(), older_than_days=older_than)
    typer.echo(
        f"{len(report.companies)} companies older than {older_than} days purged · "
        f"{report.rows_deleted} rows and {report.cache_files_deleted} cache files"
    )


@app.command()
def retry(
    run_id: Annotated[str, typer.Argument(help="The run whose errors to redo.")],
    no_site: Annotated[
        bool,
        typer.Option(
            "--no-site",
            help="Also redo the companies written off as having no website.",
        ),
    ] = False,
) -> None:
    """Re-enrich the companies of a run whose result is an error.

    An error means the tool could not look — search, a site, the model or
    SHAB failed — not that it looked and found nothing. Those companies were
    drawn, never contacted, and are redone here without drawing anything new.
    `--no-site` redoes the "no website" skips too, for a run whose search log
    shows it was throttled; a company a reviewer decided about stays as it is.
    """
    s = get_settings()
    try:
        results = asyncio.run(
            retry_errors(
                run_id, settings=s, child=build_child(settings=s), no_site=no_site
            )
        )
    except CompanyReachError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    if not results:
        typer.echo(f"nothing to retry in run {run_id}")
        return
    failing = [r for r in results if r.error_kind]
    typer.echo(
        f"{len(results)} retried · {len(results) - len(failing)} recovered · "
        f"{len(failing)} still failing"
    )
    for r in results:
        outcome = (
            f"{r.error_kind} error: {r.error_text}"
            if r.error_kind
            else (f"{r.recommendation}: {r.reason}")
        )
        typer.echo(f"  {r.uid}  {outcome}")


@app.command()
def redraft(
    run_id: Annotated[str, typer.Argument(help="The run whose cards to redraft.")],
    uid: Annotated[
        str | None,
        typer.Option(help="Only this company, whether its draft is stale or not."),
    ] = None,
) -> None:
    """Draft again the undecided send cards a run can no longer send.

    A card stops being sendable when survey_url changed after drafting, when
    the frame changed, or when its draft was never checked. The mail is
    rebuilt from what the database holds — draft and check only, no search
    and no fetch. Decided companies keep the draft they were decided on.
    """
    from company_reach.redraft import redraft_run

    s = get_settings()
    try:
        outcomes = asyncio.run(redraft_run(run_id, settings=s, uid=uid))
    except CompanyReachError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    if not outcomes:
        typer.echo(f"nothing to redraft in run {run_id}: every open card is current")
        return
    sendable = sum(o.outcome == "sendable" for o in outcomes)
    typer.echo(f"{len(outcomes)} redrafted · {sendable} sendable")
    for o in outcomes:
        typer.echo(f"  {o.uid}  {o.outcome}")


@app.command()
def enrich(
    uid: Annotated[str, typer.Option(help="The company to enrich.")],
    until: Annotated[
        str,
        typer.Option(help="How far to go: 'site', 'profile', 'contact' or 'draft'."),
    ] = "site",
    run_id: str | None = None,
) -> None:
    """Run one company through the child graph and print what it found.

    The milestone's demo, and the way to look at a single disagreement
    between the register and a website without drawing a batch.
    """
    if until not in ("site", "profile", "contact", "draft"):
        raise typer.BadParameter(
            "--until takes 'site', 'profile', 'contact' or 'draft'."
        )

    s = get_settings()
    rid = _run_id(run_id)
    child = build_child(settings=s, until=until)

    try:
        out = asyncio.run(
            child.ainvoke(
                {
                    "run_id": rid,
                    "uid": uid,
                    "goal": "",
                    "about_me": load_profile(s.profile_path).about_me,
                }
            )
        )
    except CompanyReachError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    site = out.get("site")
    if site is None:
        typer.echo(f"{uid}: no website — {out.get('reason', 'no reason recorded')}")
        return

    note = f"  ({site.evidence_note})" if site.evidence_note else ""
    typer.echo(f"{uid}  {site.url}")
    typer.echo(f"  tier      {site.tier}{note}")
    typer.echo(f"  evidence  {site.evidence}")
    typer.echo(f"  from      {site.evidence_url}")
    typer.echo(f"  pages     {len(out.get('page_urls') or [])} listed")

    profile = out.get("profile")
    if profile is None:
        return

    read = out.get("page_texts") or {}
    shells = out.get("needs_js") or []
    typer.echo(f"  read      {len(read)} of {len(out.get('pages_to_read') or [])}")
    if shells:
        typer.echo(f"  needs JS  {len(shells)}")
    typer.echo("")
    typer.echo(profile.description)
    if profile.size_signal:
        typer.echo(f"size    {profile.size_signal}")
    for address in profile.addresses:
        typer.echo(f"address {address}")
    for person in profile.persons:
        mail = person.email or "no address on the site"
        mark = "  [third party]" if person.email_offsite else ""
        role = f", {person.role}" if person.role else ""
        typer.echo(f"person  {person.name}{role} — {mail}{mark}")
    flags = [
        name
        for name, on in (
            ("distributor only", profile.distributor_only),
            ("foreign group", profile.foreign_group),
        )
        if on
    ]
    if flags:
        typer.echo(f"flags   {', '.join(flags)}")

    if "recommendation" not in out:
        return
    _echo_contact(out.get("contact"))
    typer.echo(f"\n{out['recommendation']}: {out['reason']}")
    finished = out.get("draft")
    if finished is not None:
        fits = "fits" if finished.mailto_fits else "TOO LONG for mailto"
        typer.echo(
            f"\nSubject: {finished.subject}   ({len(finished.body)} chars, {fits})"
        )
        typer.echo(finished.body)


responses_app = typer.Typer(help="The survey's answers, joined by the link's UID.")
app.add_typer(responses_app, name="responses")


@responses_app.command("import")
def responses_import(
    export: Annotated[
        Path,
        typer.Argument(
            help="The survey's CSV export: columns uid, started_at, completed_at "
            "(ISO dates or times; completed_at may be empty)."
        ),
    ],
) -> None:
    """Replace the local responses with the survey's latest export."""
    from company_reach.responses import import_responses

    s = get_settings()
    try:
        with connect(s.db_path) as conn:
            report = import_responses(conn, export)
    except (CompanyReachError, OSError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(
        f"{report.rows} responses · {report.matched} matched to a sent invitation"
        f" · {report.rows - report.matched} without one"
    )


@app.command()
def report(
    within: Annotated[
        int, typer.Option(help="Count a start only this many days after the mail.")
    ] = 21,
) -> None:
    """Sent, bounced, never, started and completed, per frame, A/B arm and
    kind of contact, with Wilson 95% intervals. Counts only; nobody named."""
    from company_reach.responses import rate, report_rows

    s = get_settings()
    with connect(s.db_path) as conn:
        groups, unmatched = report_rows(conn, within_days=within)
    if not groups:
        typer.echo("no invitation has been sent yet")
        return
    typer.echo(
        f"{'frame':<9}{'arm':<6}{'contact':<25}{'sent':>5}{'bounced':>8}"
        f"{'never':>6}{'delivered':>10}  started (≤{within} days)"
        f"{'':<12}completed"
    )
    for g in groups:
        typer.echo(
            f"{g.frame_version:<9}{g.arm:<6}{g.contact_kind:<25}{g.sent:>5}"
            f"{g.bounced:>8}{g.never:>6}{g.delivered:>10}  "
            f"{g.started:>3} {rate(g.started, g.delivered):<26}"
            f"{g.completed:>3} {rate(g.completed, g.delivered)}"
        )
    typer.echo(f"responses without a sent invitation: {unmatched}")


def _resume(rid: str, dry: bool, goal: str | None, seed: int, target: int) -> str:
    """The command that continues this run: the same options, or following
    the hint after a --dry run would start a real one."""
    parts = ["company-reach run", f"--run-id {rid}"]
    if dry:
        parts.append("--dry")
    if goal:
        parts.append(f'--goal "{goal}"')
    if seed:
        parts.append(f"--seed {seed}")
    if target > 1:
        parts.append(f"--target {target}")
    return " ".join(parts)


def _echo_result(result: CompanyResult) -> None:
    """One line per company, the moment its child returns."""
    outcome = (
        f"{result.error_kind} error: {result.error_text}"
        if result.error_kind
        else f"{result.recommendation}: {result.reason}"
    )
    typer.echo(f"  {result.uid}  {outcome}")


def _echo_contact(contact) -> None:
    typer.echo("")
    if contact is None:
        typer.echo("contact none — nobody named and no address published")
        return
    role = f", {contact.role}" if contact.role else ""
    dated = f" ({contact.source_date})" if contact.source_date else ""
    typer.echo(f"contact {contact.name or 'nobody named'}{role}")
    typer.echo(f"        from {contact.source}{dated}: {contact.source_url}")
    for i, row in enumerate(contact.addresses):
        mark = "→" if i == 0 else " "
        typer.echo(f"      {mark} {row.email} [{row.kind}]")
    if not contact.addresses:
        typer.echo("        no address")
    if contact.linkedin_lead:
        typer.echo(f"        lead {contact.linkedin_lead} (unverified)")
    for other in contact.alternatives:
        typer.echo(f"also    {other}")


if __name__ == "__main__":
    app()
