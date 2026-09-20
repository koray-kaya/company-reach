"""Command line. typer turns each function's signature into a command with
--help; the wiring to the `company-reach` executable is [project.scripts]."""

import asyncio
import uuid
from typing import Annotated

import typer

from company_reach.nodes.load_pool import load_pool
from company_reach.nodes.screen_pool import screen_pool
from company_reach.settings import get_settings
from company_reach.tools.db import init_db
from company_reach.tools.doctor import run_checks

app = typer.Typer(help="Find Swiss companies, find the person, draft the mail.")


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
