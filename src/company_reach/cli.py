"""Command line. typer turns each function's signature into a command with
--help; the wiring to the `company-reach` executable is [project.scripts]."""

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

import typer

from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.score_pool import score_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.nodes.write_criteria import format_criteria, write_criteria
from company_reach.profile import goal_hash, load_profile
from company_reach.settings import get_settings
from company_reach.tools.db import init_db, record_run
from company_reach.tools.doctor import run_checks

app = typer.Typer(help="Find Swiss companies, find the person, draft the mail.")

PROFILE_PATH = Path("profile.toml")


def _resolve_goal(explicit: str | None) -> str:
    """--goal wins; otherwise profile.toml. Trying a goal on the command line
    without editing the file is the common case while wording is still being
    worked out."""
    return explicit.strip() if explicit else load_profile(PROFILE_PATH).goal


def _run_id(explicit: str | None) -> str:
    return explicit or f"r{uuid.uuid4().hex[:8]}"


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


if __name__ == "__main__":
    app()
