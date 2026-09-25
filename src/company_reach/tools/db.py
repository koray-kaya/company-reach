"""SQLite access. One connection per call: LangGraph runs sync nodes in a
thread pool, and a sqlite3 connection must not cross threads. WAL lets the
review page read while a run writes; busy_timeout waits instead of failing
when two writers meet. `with conn:` commits on success and rolls back on an
exception — it does not close, so we close in the finally."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from company_reach.models import (
    CompanyProfile,
    CompanyRecord,
    CompanyResult,
    Contact,
    Draft,
    Score,
    SelectionCriteria,
    StoredCriteria,
)
from company_reach.screen import screen_reason
from company_reach.tools.urls import address_key

if TYPE_CHECKING:  # avoids pulling langchain into every db import
    from company_reach.tools.llm import Provenance
    from company_reach.tools.search import Asked


def _sql_address_key(value: str | None) -> str | None:
    return address_key(value) if value else None


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
    # `address_key(x)` in SQL is the Python function: an address compares
    # the same way in a query as in code (case, internationalised domains)
    conn.create_function("address_key", 1, _sql_address_key, deterministic=True)
    conn.autocommit = False
    return conn


# Columns added to a table after it first shipped. `CREATE TABLE IF NOT
# EXISTS` leaves an existing table as it was, so a database created before
# the column existed gets it here.
_ADDED_COLUMNS = {
    ("contacts", "source_date"): "TEXT",
    ("contacts", "alternatives"): "TEXT",
    ("contacts", "addresses"): "TEXT",
    ("searches", "result_count"): "INTEGER",
    ("searches", "error"): "TEXT",
    ("contacts", "salutation"): "TEXT",
    ("contacts", "salutation_origin"): "TEXT",
    # frame@1: a draft of the M6/M7 shape has none of these, so it can
    # never pass for a current one
    ("drafts", "model_text"): "TEXT",
    ("drafts", "frame_version"): "TEXT",
    ("drafts", "arm"): "TEXT",
    ("drafts", "problems"): "TEXT",
    ("ledger", "frame_version"): "TEXT",
    ("ledger", "arm"): "TEXT",
    ("ledger", "contact_kind"): "TEXT",
    ("scores", "criteria_hash"): "TEXT",
}


def _replace_ledger_of_one_row_per_company(conn: sqlite3.Connection) -> None:
    """M1 shipped `ledger` keyed by uid; M7 made it a log. Every database so
    far holds the old table empty, so it is dropped and recreated — but a
    ledger with decisions in it is the record of who was contacted, and is
    never dropped by code."""
    columns = {r["name"] for r in conn.execute("pragma table_info(ledger)")}
    if not columns or "id" in columns:
        return
    if conn.execute("select count(*) from ledger").fetchone()[0]:
        raise RuntimeError(
            "the ledger has the old one-row-per-company shape and holds "
            "decisions; migrate it by hand rather than lose who was contacted"
        )
    conn.execute("drop table ledger")


def init_db(path: Path) -> None:
    schema = files("company_reach").joinpath("schema.sql").read_text()
    conn = _open(path)
    try:
        with conn:
            _replace_ledger_of_one_row_per_company(conn)
            conn.executescript(schema)
            for (table, column), kind in _ADDED_COLUMNS.items():
                have = {r["name"] for r in conn.execute(f"pragma table_info({table})")}
                if column not in have:
                    conn.execute(f"alter table {table} add column {column} {kind}")
    finally:
        conn.close()
    _current.add(path.resolve())


# Databases whose schema this process has already brought up to date.
_current: set[Path] = set()


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """One connection, in one transaction. The first connection to a path in
    a process applies the schema, so no command can meet a database older
    than the code — a column added later exists before anything writes it."""
    if path.resolve() not in _current:
        init_db(path)
    conn = _open(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def copy_database(source: Path, target: Path) -> None:
    """A consistent copy of a live database, through SQLite's backup API: a
    plain file copy would miss what still sits in the WAL file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_companies(
    conn: sqlite3.Connection, records: list[CompanyRecord], run_id: str
) -> int:
    """Store companies, screened as they are stored. Screening used to be a
    command of its own, keyed by the import's run id: a mistyped or stale id
    screened nothing, and an unscreened company read as kept. Now every
    insert and every update carries the rules' verdict, so NULL means kept."""
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
            screen_reason(r),
            now(),
            run_id,
        )
        for r in records
    ]
    conn.executemany(
        """INSERT INTO companies (uid, name, legal_form, municipality, street,
             postal_code, city, purpose, purpose_head, screen_reason,
             imported_at, import_run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(uid) DO UPDATE SET name=excluded.name, purpose=excluded.purpose,
             purpose_head=excluded.purpose_head, street=excluded.street,
             postal_code=excluded.postal_code, city=excluded.city,
             municipality=excluded.municipality, legal_form=excluded.legal_form,
             screen_reason=excluded.screen_reason,
             imported_at=excluded.imported_at, import_run_id=excluded.import_run_id""",
        rows,
    )
    return len(rows)


def unscored_companies(
    conn: sqlite3.Connection,
    goal_hash: str,
    prompt_version: str,
    model: str,
    criteria_hash: str | None,
) -> list[CompanyRecord]:
    """Companies the rules kept and this (goal, prompt, model, criteria) has
    not scored.

    The left join is the score cache: rerunning after an interrupted pass, or
    with a longer --limit, costs nothing for work already done. `is` rather
    than `=` for the criteria, because it also matches NULL to NULL: a score
    made before criteria were stored, while its goal has none stored yet."""
    rows = conn.execute(
        """select c.uid, c.name, c.legal_form, c.municipality, c.street,
                  c.postal_code, c.city, c.purpose, c.purpose_head
             from companies c
             left join scores s
               on s.uid = c.uid and s.goal_hash = ?
              and s.prompt_version = ? and s.model = ?
              and s.criteria_hash is ?
            where c.screen_reason is null and s.uid is null
            order by c.uid""",
        (goal_hash, prompt_version, model, criteria_hash),
    ).fetchall()
    return [CompanyRecord(**dict(row)) for row in rows]


def count_scored(
    conn: sqlite3.Connection,
    goal_hash: str,
    prompt_version: str,
    model: str,
    criteria_hash: str | None,
) -> int:
    return conn.execute(
        "select count(*) from scores where goal_hash = ? and prompt_version = ? "
        "and model = ? and criteria_hash is ?",
        (goal_hash, prompt_version, model, criteria_hash),
    ).fetchone()[0]


def upsert_scores(
    conn: sqlite3.Connection,
    scores: list[Score],
    *,
    goal_hash: str,
    prompt_version: str,
    model: str,
    criteria_hash: str | None,
) -> int:
    """A company keeps one score per (goal, prompt, model). A rescore under
    new criteria replaces it, and the row then names the new criteria."""
    conn.executemany(
        """INSERT INTO scores (uid, goal_hash, prompt_version, model, score,
             reason, scored_at, criteria_hash)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(uid, goal_hash, prompt_version, model) DO UPDATE SET
             score=excluded.score, reason=excluded.reason,
             scored_at=excluded.scored_at, criteria_hash=excluded.criteria_hash""",
        [
            (
                s.uid,
                goal_hash,
                prompt_version,
                model,
                s.score,
                s.reason,
                now(),
                criteria_hash,
            )
            for s in scores
        ],
    )
    return len(scores)


# --- criteria: one set per goal (audit H10) -----------------------------------


def load_criteria(conn: sqlite3.Connection, goal_hash: str) -> StoredCriteria | None:
    row = conn.execute(
        "select * from criteria where goal_hash = ?", (goal_hash,)
    ).fetchone()
    if row is None:
        return None
    return StoredCriteria(
        criteria=SelectionCriteria.model_validate_json(row["criteria"]),
        criteria_hash=row["criteria_hash"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        created_at=row["created_at"],
    )


def score_run_criteria(conn: sqlite3.Connection, goal_hash: str) -> list[sqlite3.Row]:
    """The criteria earlier `score` commands recorded for this goal, oldest
    first. Before the criteria table, every `score` wrote its own set and
    kept it only on its `runs` row."""
    return conn.execute(
        """select id, criteria, model, prompt_versions from runs
            where goal_hash = ? and criteria is not null
            order by started_at, rowid""",
        (goal_hash,),
    ).fetchall()


def current_criteria_hash(conn: sqlite3.Connection, goal_hash: str) -> str | None:
    """The hash a score must carry to count for this goal. None while the
    goal has no criteria stored; then the scores made before criteria were
    stored, which carry NULL, are the ones that count."""
    stored = load_criteria(conn, goal_hash)
    return stored.criteria_hash if stored else None


def store_criteria(
    conn: sqlite3.Connection,
    goal_hash: str,
    criteria: SelectionCriteria,
    *,
    criteria_hash: str,
    model: str | None,
    prompt_version: str | None,
    adopt_unlinked: bool,
) -> int:
    """Store the goal's criteria, replacing any set stored before.

    The first time a goal gets criteria, its scores made before criteria
    were stored take their hash (`adopt_unlinked`). They are the baseline
    the owner has; without this, a database from before the table would
    lose every score at once. Not when new criteria were asked for, since
    the point then is to score again. Returns how many scores took it.

    A set that is replaced moves to `criteria_history`, so the rules the
    pool was scored against can still be read, and put back by hand."""
    first = load_criteria(conn, goal_hash) is None
    conn.execute(
        """INSERT INTO criteria_history (goal_hash, criteria, criteria_hash,
             model, prompt_version, created_at, replaced_at)
           SELECT goal_hash, criteria, criteria_hash, model, prompt_version,
                  created_at, ? FROM criteria WHERE goal_hash = ?""",
        (now(), goal_hash),
    )
    conn.execute(
        """INSERT INTO criteria (goal_hash, criteria, criteria_hash, model,
             prompt_version, created_at) VALUES (?,?,?,?,?,?)
           ON CONFLICT(goal_hash) DO UPDATE SET criteria=excluded.criteria,
             criteria_hash=excluded.criteria_hash, model=excluded.model,
             prompt_version=excluded.prompt_version,
             created_at=excluded.created_at""",
        (
            goal_hash,
            criteria.model_dump_json(),
            criteria_hash,
            model,
            prompt_version,
            now(),
        ),
    )
    if not (first and adopt_unlinked):
        return 0
    return conn.execute(
        "update scores set criteria_hash = ? "
        "where goal_hash = ? and criteria_hash is null",
        (criteria_hash, goal_hash),
    ).rowcount


def record_run(
    path: Path,
    run_id: str,
    goal: str,
    criteria: StoredCriteria,
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
                criteria.model,
                json.dumps({"criteria": criteria.prompt_version}),
                criteria.criteria.model_dump_json(),
                now(),
                "scoring",
            ),
        )


# What a run may draw, written once. `draw_batch`, the guard in `run` and
# `status` all read this text, so they cannot disagree about it: the guard
# used to count any score, and passed a pool nothing could be drawn from.
_DRAWABLE = """
             from companies c
             join scores s
               on s.uid = c.uid and s.goal_hash = :goal_hash
              and s.prompt_version = :prompt_version and s.model = :model
              and s.criteria_hash is :criteria_hash
            where c.screen_reason is null
              and s.score >= :min_score
              -- not already drawn in THIS run, or the loop would redraw it
              and c.uid not in (select uid from seen where run_id = :run_id)
              -- a reviewer decided about it, or it may never be contacted
              and c.uid not in (select uid from ledger)
              and c.uid not in (select key from suppression)
              -- never drawn at all, or drawn only by earlier runs that failed
              -- on it: an errored company was never contacted and has no
              -- draft, so there is nothing to protect it from. "Only" is the
              -- point — a company another run finished after an error keeps
              -- that old error row, and may already have been written to.
              and (c.uid not in (select uid from seen)
                   or (c.uid in (select uid from results
                                  where error_kind is not null
                                    -- still in flight elsewhere, or crashed:
                                    -- its own run id finishes it, not ours
                                    and error_kind <> 'interrupted'
                                    and run_id <> :run_id)
                       and c.uid not in (select uid from results
                                          where error_kind is null)))"""


def draw_batch(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    batch_no: int,
    goal_hash: str,
    prompt_version: str,
    model: str,
    criteria_hash: str | None,
    min_score: int,
    limit: int,
) -> list[str]:
    """The uids for one batch, best score first. Empty means the pool is
    exhausted: nothing left that clears `min_score`.

    On a rerun of the same run_id and batch_no the recorded batch comes back
    unchanged — a resumed run must repeat the batch it drew, not draw a new
    one. Order within a batch is not preserved on that path and does not
    matter: the whole batch fans out in one superstep.

    No tie-breaking. Scoring order was already shuffled with the run's seed,
    so equal scores sit in random order; adding `, uid` here would undo that
    and bias every batch towards low UIDs, which are the canton's oldest
    firms.
    """
    recorded = [
        r["uid"]
        for r in conn.execute(
            "select uid from seen where run_id = ? and batch_no = ? order by uid",
            (run_id, batch_no),
        )
    ]
    if recorded:
        return recorded

    rows = conn.execute(
        f"select c.uid {_DRAWABLE} order by s.score desc limit :limit",
        {
            "run_id": run_id,
            "goal_hash": goal_hash,
            "prompt_version": prompt_version,
            "model": model,
            "criteria_hash": criteria_hash,
            "min_score": min_score,
            "limit": limit,
        },
    ).fetchall()
    return [r["uid"] for r in rows]


@dataclass(frozen=True)
class Standing:
    """How the pool stands against one score key: what `run` could draw,
    and if nothing, why not. Each kept company counts once, under the first
    of current, earlier_criteria, other_prompt, other_model, unscored."""

    pooled: int
    kept: int
    current: int  # scored under this goal, prompt version, model, criteria
    best: int | None  # the highest current score
    clear: int  # current scores at or above the bar
    drawable: int  # of those, what this run may still draw
    earlier_criteria: int
    other_prompt: int
    other_prompt_versions: list[str]
    other_model: int
    unscored: int


def pool_standing(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    goal_hash: str,
    prompt_version: str,
    model: str,
    criteria_hash: str | None,
    min_score: int,
) -> Standing:
    key = {
        "run_id": run_id,
        "goal_hash": goal_hash,
        "prompt_version": prompt_version,
        "model": model,
        "criteria_hash": criteria_hash,
        "min_score": min_score,
    }
    kinds = {
        r["kind"]: r["n"]
        for r in conn.execute(
            """select case
                 when exists (select 1 from scores s where s.uid = c.uid
                                and s.goal_hash = :goal_hash
                                and s.prompt_version = :prompt_version
                                and s.model = :model
                                and s.criteria_hash is :criteria_hash)
                   then 'current'
                 when exists (select 1 from scores s where s.uid = c.uid
                                and s.goal_hash = :goal_hash
                                and s.prompt_version = :prompt_version
                                and s.model = :model)
                   then 'earlier_criteria'
                 when exists (select 1 from scores s where s.uid = c.uid
                                and s.goal_hash = :goal_hash and s.model = :model)
                   then 'other_prompt'
                 when exists (select 1 from scores s where s.uid = c.uid
                                and s.goal_hash = :goal_hash)
                   then 'other_model'
                 else 'unscored' end as kind,
                 count(*) as n
                 from companies c where c.screen_reason is null group by kind""",
            key,
        )
    }
    best, clear = conn.execute(
        """select max(s.score), count(*) filter (where s.score >= :min_score)
             from scores s join companies c on c.uid = s.uid
            where c.screen_reason is null and s.goal_hash = :goal_hash
              and s.prompt_version = :prompt_version and s.model = :model
              and s.criteria_hash is :criteria_hash""",
        key,
    ).fetchone()
    versions = [
        r[0]
        for r in conn.execute(
            """select distinct prompt_version from scores
                where goal_hash = :goal_hash and model = :model
                  and prompt_version <> :prompt_version
                order by prompt_version""",
            key,
        )
    ]
    return Standing(
        pooled=conn.execute("select count(*) from companies").fetchone()[0],
        kept=sum(kinds.values()),
        current=kinds.get("current", 0),
        best=best,
        clear=clear,
        drawable=conn.execute(f"select count(*) {_DRAWABLE}", key).fetchone()[0],
        earlier_criteria=kinds.get("earlier_criteria", 0),
        other_prompt=kinds.get("other_prompt", 0),
        other_prompt_versions=versions,
        other_model=kinds.get("other_model", 0),
        unscored=kinds.get("unscored", 0),
    )


def status_by_municipality(
    conn: sqlite3.Connection,
    *,
    goal_hash: str,
    prompt_version: str,
    model: str,
    criteria_hash: str | None,
    min_score: int,
) -> list[sqlite3.Row]:
    """One row per municipality: companies pooled, kept by the rules, scored
    under the current key, drawable by a new run, drawn, sent, and send
    cards nobody has decided yet. Drawable is `_DRAWABLE` for a run that has
    drawn nothing (run id ""), so it says what the next `run` could take."""
    return conn.execute(
        f"""with drawable as (select c.uid {_DRAWABLE}),
                 current as (
                   select uid from scores
                    where goal_hash = :goal_hash
                      and prompt_version = :prompt_version and model = :model
                      and criteria_hash is :criteria_hash),
                 -- a company's decision is its latest ledger row, unless undone
                 decided as (
                   select l.uid from ledger l
                    where l.id = (select max(id) from ledger where uid = l.uid)
                      and l.status <> 'undone')
            select c.municipality,
                   count(*) as pooled,
                   sum(c.screen_reason is null) as kept,
                   sum(c.screen_reason is null
                       and c.uid in (select uid from current)) as scored,
                   sum(c.uid in (select uid from drawable)) as drawable,
                   sum(c.uid in (select uid from seen)) as drawn,
                   sum(c.uid in (select uid from ledger where status = 'sent'))
                     as sent,
                   sum(c.uid in (select uid from results
                                  where recommendation = 'send')
                       and c.uid not in (select uid from decided)) as undecided
              from companies c
             group by c.municipality
             order by c.municipality""",
        {
            "run_id": "",
            "goal_hash": goal_hash,
            "prompt_version": prompt_version,
            "model": model,
            "criteria_hash": criteria_hash,
            "min_score": min_score,
        },
    ).fetchall()


def record_seen(
    conn: sqlite3.Connection, uids: list[str], *, run_id: str, batch_no: int
) -> int:
    """Mark a batch as drawn. `seen.uid` is the primary key, so a company
    recovered by a later run updates its row rather than inserting a second:
    the row says which run last drew it, which is exactly what `draw_batch`
    reads to keep a recovered company out of its own run's next batch."""
    conn.executemany(
        """INSERT INTO seen (uid, run_id, batch_no, drawn_at) VALUES (?,?,?,?)
           ON CONFLICT(uid) DO UPDATE SET
             run_id=excluded.run_id, batch_no=excluded.batch_no,
             drawn_at=excluded.drawn_at""",
        [(u, run_id, batch_no, now()) for u in uids],
    )
    return len(uids)


def record_pending(conn: sqlite3.Connection, uids: list[str], *, run_id: str) -> None:
    """A result row for every company of a batch, written with `seen` in one
    transaction. It reads as an error until the child replaces it, so a crash
    leaves the company retryable instead of drawn and forgotten (audit H8).
    INSERT OR IGNORE: on a rerun, a company that already finished keeps its
    row."""
    conn.executemany(
        """INSERT OR IGNORE INTO results (run_id, uid, recommendation, reason,
             error_kind, error_text, finished_at) VALUES (?,?,?,?,?,?,?)""",
        [
            (
                run_id,
                uid,
                None,
                None,
                "interrupted",
                "not finished: still running, or the run stopped. "
                f"`company-reach retry {run_id}` redoes it",
                now(),
            )
            for uid in uids
        ],
    )


def record_result(
    conn: sqlite3.Connection, result: CompanyResult, *, run_id: str
) -> None:
    """One row per (run, company). A retry of a company that errored replaces
    its row, so a recovered company leaves no error behind."""
    conn.execute(
        """INSERT INTO results (run_id, uid, recommendation, reason,
             error_kind, error_text, finished_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(run_id, uid) DO UPDATE SET
             recommendation=excluded.recommendation, reason=excluded.reason,
             error_kind=excluded.error_kind, error_text=excluded.error_text,
             finished_at=excluded.finished_at""",
        (
            run_id,
            result.uid,
            result.recommendation,
            result.reason,
            result.error_kind,
            result.error_text,
            now(),
        ),
    )


def result_for(
    conn: sqlite3.Connection, uid: str, *, run_id: str
) -> CompanyResult | None:
    row = conn.execute(
        """select uid, recommendation, reason, error_kind, error_text
             from results where run_id = ? and uid = ?""",
        (run_id, uid),
    ).fetchone()
    return CompanyResult(**dict(row)) if row else None


def count_sendable(conn: sqlite3.Connection, run_id: str) -> int:
    """From the table, never from state. A resumed run has an empty `results`
    list in state and a full table; counting state would draw a fresh batch
    on every rerun (audit A1, third P0 finding)."""
    return conn.execute(
        "select count(*) from results where run_id = ? and recommendation = 'send'",
        (run_id,),
    ).fetchone()[0]


def company_by_uid(conn: sqlite3.Connection, uid: str) -> CompanyRecord | None:
    row = conn.execute(
        """select uid, name, legal_form, municipality, street, postal_code,
                  city, purpose, purpose_head
             from companies where uid = ?""",
        (uid,),
    ).fetchone()
    return CompanyRecord(**dict(row)) if row else None


def upsert_profile(
    conn: sqlite3.Connection, run_id: str, uid: str, profile: CompanyProfile
) -> None:
    """One row per (run, company), the profile stored as JSON.

    JSON rather than a column each: nothing queries inside a profile — it is
    read back whole or not at all — and a column per field would have to
    change every time the extraction schema does.
    """
    conn.execute(
        """INSERT INTO profiles (run_id, uid, profile) VALUES (?,?,?)
           ON CONFLICT(run_id, uid) DO UPDATE SET profile=excluded.profile""",
        (run_id, uid, profile.model_dump_json()),
    )


def profile_by_uid(
    conn: sqlite3.Connection, run_id: str, uid: str
) -> CompanyProfile | None:
    """None means this company never got as far as being read. A company
    whose pages named nobody comes back as a profile with no persons, which
    is a finding and must not read the same as the absence."""
    row = conn.execute(
        "select profile from profiles where run_id = ? and uid = ?", (run_id, uid)
    ).fetchone()
    return CompanyProfile.model_validate_json(row["profile"]) if row else None


def record_page(
    conn: sqlite3.Connection,
    url: str,
    *,
    status: int | None,
    text: str,
    raw_path: str,
) -> None:
    """Index one page that was read: what it said, and where its bytes are.

    The url is the key because this table indexes the disk cache and the
    cache holds one copy per url — a rerun replaces the row rather than
    growing the table. The cleaned text is kept here so that the review page
    and a later run can see what the model was given without re-fetching the
    site or re-running the extractor over stored HTML.
    """
    conn.execute(
        """INSERT INTO pages (url, fetched_at, status, text, raw_path)
           VALUES (?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
             fetched_at=excluded.fetched_at, status=excluded.status,
             text=excluded.text, raw_path=excluded.raw_path""",
        (url, now(), status, text, raw_path),
    )


def record_contact(
    conn: sqlite3.Connection, run_id: str, uid: str, contact: Contact
) -> int:
    """One contact per (run, company); a retry replaces it rather than adding
    a second. Returns the row id, which the draft refers to."""
    conn.execute("delete from contacts where run_id = ? and uid = ?", (run_id, uid))
    cur = conn.execute(
        """INSERT INTO contacts (run_id, uid, name, role, email, email_kind,
             source, source_url, source_date, linkedin_lead, alternatives,
             addresses, salutation, salutation_origin)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            uid,
            contact.name,
            contact.role,
            contact.email,
            contact.email_kind,
            contact.source,
            contact.source_url,
            contact.source_date,
            contact.linkedin_lead,
            json.dumps(contact.alternatives, ensure_ascii=False),
            json.dumps([a.model_dump(exclude_none=True) for a in contact.addresses]),
            contact.salutation,
            contact.salutation_origin,
        ),
    )
    return cur.lastrowid


def contact_for(
    conn: sqlite3.Connection, run_id: str, uid: str
) -> tuple[int, Contact] | None:
    """The company's latest contact in a run, with its row id (the draft
    refers to it), or None when the run found nobody to write to."""
    row = conn.execute(
        "select * from contacts where run_id = ? and uid = ? order by id desc limit 1",
        (run_id, uid),
    ).fetchone()
    if row is None:
        return None
    addresses = json.loads(row["addresses"] or "[]")
    if not addresses and row["email"] and row["email_kind"]:
        # a run from before M7 stored only the chosen address
        addresses = [{"email": row["email"], "kind": row["email_kind"]}]
    return row["id"], Contact(
        name=row["name"],
        role=row["role"],
        email=row["email"],
        email_kind=row["email_kind"],
        source=row["source"],
        source_url=row["source_url"],
        source_date=row["source_date"],
        linkedin_lead=row["linkedin_lead"],
        alternatives=json.loads(row["alternatives"] or "[]"),
        addresses=addresses,
        salutation=row["salutation"],
        salutation_origin=row["salutation_origin"],
    )


def readdress_contact(
    conn: sqlite3.Connection, run_id: str, uid: str, contact: Contact
) -> None:
    """The reviewer chose another of the card's addresses: the company's
    latest contact row now says who the mail is for and where it goes. The
    row keeps its id, so the draft still points at it."""
    conn.execute(
        """update contacts set name = ?, role = ?, email = ?, email_kind = ?,
             salutation = ?, salutation_origin = ?, alternatives = ?,
             addresses = ?
            where id = (select max(id) from contacts where run_id = ? and uid = ?)""",
        (
            contact.name,
            contact.role,
            contact.email,
            contact.email_kind,
            contact.salutation,
            contact.salutation_origin,
            json.dumps(contact.alternatives, ensure_ascii=False),
            json.dumps([a.model_dump(exclude_none=True) for a in contact.addresses]),
            run_id,
            uid,
        ),
    )


def set_salutation(
    conn: sqlite3.Connection, run_id: str, uid: str, salutation: str
) -> None:
    """The reviewer's choice on the card, on the company's latest contact."""
    conn.execute(
        """update contacts set salutation = ?, salutation_origin = 'reviewer'
            where id = (select max(id) from contacts where run_id = ? and uid = ?)""",
        (salutation, run_id, uid),
    )


def record_draft(
    conn: sqlite3.Connection,
    run_id: str,
    uid: str,
    draft: Draft,
    *,
    contact_id: int | None,
    provenance: "Provenance",
) -> None:
    """One draft per (run, company); a regeneration or a retry replaces it."""
    conn.execute("delete from drafts where run_id = ? and uid = ?", (run_id, uid))
    conn.execute(
        """INSERT INTO drafts (run_id, uid, contact_id, subject, body,
             mailto_fits, prompt_version, model, created_at, model_text,
             frame_version, arm)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            uid,
            contact_id,
            draft.subject,
            draft.body,
            int(draft.mailto_fits),
            provenance.prompt_version,
            provenance.model,
            now(),
            draft.model_text,
            draft.frame_version,
            draft.arm,
        ),
    )


def rewrite_draft(
    conn: sqlite3.Connection,
    draft_id: int,
    draft: Draft,
    *,
    found: list[str],
    contact_id: int | None = None,
) -> None:
    """The same draft rebuilt by code around the same sentence — the card's
    salutation or address, or `redraft` after the profile or the contact
    changed — with the outcome of checking it again. `contact_id` moves the
    draft to the contact it now addresses."""
    conn.execute(
        """update drafts set subject = ?, body = ?, mailto_fits = ?,
             model_text = ?, frame_version = ?, arm = ?, problems = ?,
             contact_id = coalesce(?, contact_id)
            where id = ?""",
        (
            draft.subject,
            draft.body,
            int(draft.mailto_fits),
            draft.model_text,
            draft.frame_version,
            draft.arm,
            "; ".join(found),
            contact_id,
            draft_id,
        ),
    )


def record_draft_check(
    conn: sqlite3.Connection, run_id: str, uid: str, found: list[str]
) -> None:
    """`check_draft`'s outcome on the company's draft: "" when it passed,
    the rules it broke otherwise. A draft that was never checked keeps
    null, and the card sends neither."""
    conn.execute(
        "update drafts set problems = ? where run_id = ? and uid = ?",
        ("; ".join(found), run_id, uid),
    )


def delete_draft(conn: sqlite3.Connection, run_id: str, uid: str) -> None:
    """A draft that failed its checks twice is not kept: nothing that
    failed them should be one click from being sent."""
    conn.execute("delete from drafts where run_id = ? and uid = ?", (run_id, uid))


# A company a reviewer decided about — sent, skipped, never, bounced, even a
# skip since undone — or one on the never-again list. Nothing more is
# collected about it: `retry`, `retry --no-site`, a resumed batch and
# `enrich` all read this one rule (audit H7), and `closed_because` is the
# same rule for one company.
_CLOSED = "(uid in (select uid from ledger) or uid in (select key from suppression))"


def no_site_uids(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """The companies of a run written off as having no website, that are
    not closed: what `retry --no-site` redoes after a run whose search
    turned out to have been throttled (#20)."""
    return [
        r["uid"]
        for r in conn.execute(
            f"""select uid from results
                 where run_id = ? and recommendation = 'skip'
                   and reason like 'no website found%'
                   and not {_CLOSED}
                 order by uid""",
            (run_id,),
        )
    ]


def errored_uids(conn: sqlite3.Connection, run_id: str) -> list[str]:
    """The companies of a run whose result is an error: what `retry` redoes.
    Not a closed one — retrying it would collect data about it for
    nothing, or against a deletion request."""
    return [
        r["uid"]
        for r in conn.execute(
            f"""select uid from results
                 where run_id = ? and error_kind is not null
                   and not {_CLOSED}
                 order by uid""",
            (run_id,),
        )
    ]


def closed_because(conn: sqlite3.Connection, uid: str) -> str | None:
    """Why nothing more may be collected about this company, or None: the
    rule of `_CLOSED`, for one company, in words a command can print."""
    if is_suppressed(conn, uid):
        return "it is on the never-again list"
    row = conn.execute(
        "select status from ledger where uid = ? order by id desc limit 1", (uid,)
    ).fetchone()
    if row is not None:
        return f"a reviewer decided about it ({row['status']})"
    return None


# --- the ledger and suppression (M7) ----------------------------------------

DECISIONS = ("sent", "skipped", "never", "undone")


def record_decision(
    conn: sqlite3.Connection,
    uid: str,
    status: str,
    *,
    address: str | None = None,
    draft_id: int | None = None,
    run_id: str | None = None,
    note: str | None = None,
    decided_at: str | None = None,
    frame_version: str | None = None,
    arm: str | None = None,
    contact_kind: str | None = None,
) -> int:
    """Append one decision. Nothing in the ledger is ever updated or
    deleted by the page: undoing a skip is an `undone` row after it.

    A `sent` row carries the draft's frame and arm and the kind of contact
    it went to, copied here because `forget` and `purge` delete drafts and
    contacts but keep the ledger — and the survey's answers are compared by
    them."""
    if status not in DECISIONS:
        raise ValueError(f"status must be one of {DECISIONS}, not {status!r}")
    cur = conn.execute(
        """INSERT INTO ledger (uid, status, address, draft_id, run_id, note,
             decided_at, frame_version, arm, contact_kind)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            uid,
            status,
            address,
            draft_id,
            run_id,
            note,
            decided_at or now(),
            frame_version,
            arm,
            contact_kind,
        ),
    )
    return cur.lastrowid


def decision_for(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    """The company's current decision, or None while it is undecided —
    never decided, or its last decision undone."""
    row = conn.execute(
        "select * from ledger where uid = ? order by id desc limit 1", (uid,)
    ).fetchone()
    return None if row is None or row["status"] == "undone" else row


def was_contacted(conn: sqlite3.Connection, uid: str) -> bool:
    """Any `sent` row, ever. "Contacted once, ever" rests on this."""
    return (
        conn.execute(
            "select 1 from ledger where uid = ? and status = 'sent' limit 1", (uid,)
        ).fetchone()
        is not None
    )


def sent_this_month(conn: sqlite3.Connection, *, today: str | None = None) -> int:
    """How many were sent this calendar month. A number the page shows, not
    a limit: there is no cap by design."""
    month = (today or now())[:7]
    return conn.execute(
        "select count(*) from ledger"
        " where status = 'sent' and substr(decided_at, 1, 7) = ?",
        (month,),
    ).fetchone()[0]


def _key(key: str) -> str:
    return address_key(key) if "@" in key else key.strip()


def suppress(conn: sqlite3.Connection, key: str, *, reason: str) -> None:
    """A uid or an e-mail address that is never contacted again. Permanent."""
    conn.execute(
        "INSERT OR IGNORE INTO suppression (key, reason, added_at) VALUES (?,?,?)",
        (_key(key), reason, now()),
    )


def is_suppressed(conn: sqlite3.Connection, key: str) -> bool:
    return (
        conn.execute("select 1 from suppression where key = ?", (_key(key),)).fetchone()
        is not None
    )


def record_site(
    conn: sqlite3.Connection,
    run_id: str,
    uid: str,
    *,
    url: str | None,
    tier: str | None = None,
    evidence: str | None = None,
    evidence_url: str | None = None,
    note: str | None = None,
    queries: list[str],
    candidates: list[str],
) -> None:
    """One row per (run, company): the site find_site chose and its
    evidence, or `url` None with the searches it tried."""
    conn.execute(
        """INSERT INTO sites (run_id, uid, url, tier, evidence, evidence_url,
             note, queries, candidates) VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(run_id, uid) DO UPDATE SET url=excluded.url,
             tier=excluded.tier, evidence=excluded.evidence,
             evidence_url=excluded.evidence_url, note=excluded.note,
             queries=excluded.queries, candidates=excluded.candidates""",
        (
            run_id,
            uid,
            url,
            tier,
            evidence,
            evidence_url,
            note,
            json.dumps(queries, ensure_ascii=False),
            json.dumps(candidates),
        ),
    )


def site_record(conn: sqlite3.Connection, run_id: str, uid: str) -> sqlite3.Row | None:
    return conn.execute(
        "select * from sites where run_id = ? and uid = ?", (run_id, uid)
    ).fetchone()


# --- the search log (#20) ----------------------------------------------------

_LOGGED_URLS = 10


def record_searches(
    conn: sqlite3.Connection, run_id: str, uid: str, asked: "list[Asked]"
) -> None:
    """One row per provider asked for one of find_site's queries, errors
    included. A new attempt replaces the company's log for the run, as its
    site record and contact are replaced.

    URLs are kept for SearXNG only, the first ten. Brave's terms forbid
    storing its results, so a Brave row keeps the query, the count and any
    error — enough to see what a "no website" rests on."""
    conn.execute("delete from searches where run_id = ? and uid = ?", (run_id, uid))
    conn.executemany(
        """INSERT INTO searches (run_id, uid, query, provider, results,
             unresponsive, result_count, error, at) VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            (
                run_id,
                uid,
                a.query,
                a.provider,
                json.dumps([r.url for r in a.results[:_LOGGED_URLS]])
                if a.provider == "searxng"
                else None,
                json.dumps(a.unresponsive),
                len(a.results),
                a.error,
                now(),
            )
            for a in asked
        ],
    )


def search_log(conn: sqlite3.Connection, run_id: str, uid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "select * from searches where run_id = ? and uid = ? order by id",
        (run_id, uid),
    ).fetchall()


def count_brave_queries(conn: sqlite3.Connection, run_id: str) -> int:
    """Brave is paid by the query; a run reports what it spent."""
    return conn.execute(
        "select count(*) from searches where run_id = ? and provider = 'brave'",
        (run_id,),
    ).fetchone()[0]
