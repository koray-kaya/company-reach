"""SQLite access. One connection per call: LangGraph runs sync nodes in a
thread pool, and a sqlite3 connection must not cross threads. WAL lets the
review page read while a run writes; busy_timeout waits instead of failing
when two writers meet. `with conn:` commits on success and rolls back on an
exception — it does not close, so we close in the finally."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from company_reach.models import CompanyRecord, Score, SelectionCriteria

if TYPE_CHECKING:  # avoids pulling langchain into every db import
    from company_reach.tools.llm import Provenance


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The PRAGMAs must run outside a transaction ("cannot change into wal mode
    # from within a transaction"), and autocommit=False opens one before the
    # first statement. So: set them first, then take over transaction control.
    conn = sqlite3.connect(path, autocommit=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.autocommit = False
    return conn


def init_db(path: Path) -> None:
    schema = files("company_reach").joinpath("schema.sql").read_text()
    conn = _open(path)
    try:
        with conn:
            conn.executescript(schema)
    finally:
        conn.close()


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = _open(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_companies(
    conn: sqlite3.Connection, records: list[CompanyRecord], run_id: str
) -> int:
    rows = [
        (
            r.uid,
            r.name,
            r.legal_form,
            r.municipality,
            r.street,
            r.postal_code,
            r.city,
            r.purpose,
            r.purpose_head,
            now(),
            run_id,
        )
        for r in records
    ]
    conn.executemany(
        """INSERT INTO companies (uid, name, legal_form, municipality, street,
             postal_code, city, purpose, purpose_head, imported_at, import_run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(uid) DO UPDATE SET name=excluded.name, purpose=excluded.purpose,
             purpose_head=excluded.purpose_head, street=excluded.street,
             postal_code=excluded.postal_code, city=excluded.city,
             imported_at=excluded.imported_at, import_run_id=excluded.import_run_id""",
        rows,
    )
    return len(rows)


def unscored_companies(
    conn: sqlite3.Connection, goal_hash: str, prompt_version: str, model: str
) -> list[CompanyRecord]:
    """Companies the rules kept and this (goal, prompt, model) has not scored.

    The left join is the score cache: rerunning after an interrupted pass, or
    with a longer --limit, costs nothing for work already done."""
    rows = conn.execute(
        """select c.uid, c.name, c.legal_form, c.municipality, c.street,
                  c.postal_code, c.city, c.purpose, c.purpose_head
             from companies c
             left join scores s
               on s.uid = c.uid and s.goal_hash = ?
              and s.prompt_version = ? and s.model = ?
            where c.screen_reason is null and s.uid is null
            order by c.uid""",
        (goal_hash, prompt_version, model),
    ).fetchall()
    return [CompanyRecord(**dict(row)) for row in rows]


def count_scored(
    conn: sqlite3.Connection, goal_hash: str, prompt_version: str, model: str
) -> int:
    return conn.execute(
        "select count(*) from scores where goal_hash = ? and prompt_version = ? "
        "and model = ?",
        (goal_hash, prompt_version, model),
    ).fetchone()[0]


def upsert_scores(
    conn: sqlite3.Connection,
    scores: list[Score],
    *,
    goal_hash: str,
    prompt_version: str,
    model: str,
) -> int:
    conn.executemany(
        """INSERT INTO scores (uid, goal_hash, prompt_version, model, score,
             reason, scored_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(uid, goal_hash, prompt_version, model) DO UPDATE SET
             score=excluded.score, reason=excluded.reason,
             scored_at=excluded.scored_at""",
        [
            (s.uid, goal_hash, prompt_version, model, s.score, s.reason, now())
            for s in scores
        ],
    )
    return len(scores)


def record_run(
    path: Path,
    run_id: str,
    goal: str,
    criteria: SelectionCriteria,
    provenance: "Provenance",
    *,
    seed: int,
) -> None:
    """Write the run's own record before any scoring happens.

    The criteria are stored as JSON rather than left inside the prompt, so the
    question "why did this company score 8?" has an answer months later: these
    were the rules, this was the prompt version, this was the model."""
    from company_reach.profile import goal_hash

    with connect(path) as conn:
        conn.execute(
            """INSERT INTO runs (id, goal, goal_hash, seed, batch_size, model,
                 prompt_versions, criteria, started_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET criteria=excluded.criteria,
                 prompt_versions=excluded.prompt_versions, model=excluded.model""",
            (
                run_id,
                goal,
                goal_hash(goal),
                seed,
                None,
                provenance.model,
                json.dumps({provenance.prompt: provenance.prompt_version}),
                criteria.model_dump_json(),
                now(),
                "scoring",
            ),
        )
