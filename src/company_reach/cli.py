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
from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.score_pool import score_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.nodes.write_criteria import format_criteria, write_criteria
from company_reach.profile import goal_hash, load_profile
from company_reach.settings import get_settings
from company_reach.tools.db import connect, init_db, record_run
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


def _require_a_scored_pool(s, goal: str) -> None:
    """A run over an empty database would print a row of zeros and look like
    a working run that found nothing. Name the three commands instead."""
    with connect(s.db_path) as conn:
        scored = conn.execute(
            "select count(*) from scores where goal_hash = ? and model = ?",
            (goal_hash(goal), s.llm_model),
        ).fetchone()[0]
    if scored == 0:
        typer.echo(
            "No company is scored for this goal yet. Run `pool`, then `screen`,"
            " then `score` before `run`.",
            err=True,
        )
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


@app.command()
def criteria(goal: str | None = None) -> None:
    """Show the selection criteria a goal produces, before scoring anything."""
    s = get_settings()
    text = _resolve_goal(goal)
    result, prov = asyncio.run(write_criteria(text, settings=s))
    typer.echo(f"goal   {text}")
    typer.echo(f"hash   {goal_hash(text)}")
    typer.echo(
        f"model  {prov.model} · {prov.prompt}@{prov.prompt_version} · "
        f"{prov.seconds:.1f}s"
    )
    typer.echo("")
    typer.echo(format_criteria(result))


@app.command()
def score(
    goal: str | None = None,
    limit: int | None = None,
    seed: int = 0,
    run_id: str | None = None,
) -> None:
    """Score screened companies against the goal. Incremental and resumable:
    already-scored companies cost nothing, so run it again to score more."""
    s = get_settings()
    text = _resolve_goal(goal)
    rid = _run_id(run_id)

    criteria_result, prov = asyncio.run(write_criteria(text, settings=s))
    typer.echo(format_criteria(criteria_result))
    typer.echo("")

    record_run(s.db_path, rid, text, criteria_result, prov, seed=seed)
    report = asyncio.run(
        score_pool(rid, text, criteria_result, settings=s, limit=limit, seed=seed)
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
    run_id: Annotated[str, typer.Option(help="The run whose companies to screen.")],
) -> None:
    """Apply the rule-based exclusions to the companies of a run."""
    s = get_settings()
    kept, dropped = screen_pool(run_id, settings=s)
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
) -> None:
    """Draw batches of the best-scoring companies and work through them.

    The pool stages are separate commands, so this starts from a database that
    `pool`, `screen` and `score` have already filled. That is what keeps
    `--dry` offline and quick enough to demonstrate.
    """
    s = get_settings()
    text = _resolve_goal(goal)
    rid = _run_id(run_id)
    _require_a_scored_pool(s, text)

    start_manifest(rid, settings=s, goal=text, seed=seed)
    state = initial_state(
        run_id=rid,
        goal=text,
        # --goal overrides the goal only; the drafts still say who writes
        about_me=load_profile(s.profile_path).about_me,
        municipality="",
        settings=s,
        seed=seed,
    )

    try:
        child = build_stub_child() if dry else build_child(settings=s)
        out = asyncio.run(run_graph(state, settings=s, child=child, dry=dry))
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        finish_manifest(rid, settings=s, status="failed", counts={}, reason=reason)
        typer.echo(f"run {rid} failed — {reason}", err=True)
        raise typer.Exit(1) from error

    counts = {
        "batches_drawn": out["batches_drawn"],
        "results": len(out["results"]),
        "sendable": out["sendable_count"],
        "errors": sum(1 for r in out["results"] if r.error_kind),
    }
    finish_manifest(rid, settings=s, status="done", counts=counts)

    typer.echo(
        f"{counts['batches_drawn']} batches · {counts['results']} companies · "
        f"{counts['errors']} errors · {counts['sendable']} sendable"
        + ("  · pool exhausted" if out["pool_exhausted"] else "")
    )
    typer.echo(f"manifest: {manifest_path(rid, settings=s)}")
    if counts["errors"] and not dry:
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
) -> None:
    """Re-enrich the companies of a run whose result is an error.

    An error means the tool could not look — search, a site, the model or
    SHAB failed — not that it looked and found nothing. Those companies were
    drawn, never contacted, and are redone here without drawing anything new.
    """
    s = get_settings()
    try:
        results = asyncio.run(
            retry_errors(run_id, settings=s, child=build_child(settings=s))
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
