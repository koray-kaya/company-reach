"""The survey's answers, joined to the ledger by the UID in the link.

Every invitation links to `<survey_url>/?c=<UID>`, so each started or
completed response ties to one `sent` row: no second mail, no tracking
pixel, and opens are not measured (the mail is plain text, sent by hand).
`responses import` reads the survey's export — one row per UID with
`started_at` and `completed_at` — into a local table; `report` prints only
counts, per frame version, A/B arm and kind of contact.

Each rate carries its Wilson 95% interval. At thesis scale a start rate of
5 in 100 is anything from 2% to 11%, and a report that prints "5.0%" alone
invites reading a difference into noise (the measurement plan's
pre-registered rule decides the A/B, not this table).

Delivered is sent minus bounced; `bounced` becomes a decision in a later
phase and counts zero until then. "Never" counts sent companies that are
now on the never-again list — a «Nein» reply ends in `forget`.
"""

import csv
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from company_reach.errors import CompanyReachError
from company_reach.tools.db import now
from company_reach.tools.invitation import compact_uid

_COLUMNS = ("uid", "started_at", "completed_at")


@dataclass(frozen=True)
class ImportReport:
    rows: int  # distinct UIDs in the export
    matched: int  # of them, with a 'sent' row in the ledger


@dataclass(frozen=True)
class Group:
    frame_version: str
    arm: str
    contact_kind: str
    sent: int
    bounced: int
    never: int
    started: int
    completed: int

    @property
    def delivered(self) -> int:
        return self.sent - self.bounced


def _when(value: str, line: int) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError as e:
        raise CompanyReachError(
            f"line {line}: {value!r} is not an ISO date or time"
        ) from e


def import_responses(conn: sqlite3.Connection, path: Path) -> ImportReport:
    """Replace the table with the export at `path`. The survey exports
    everything so far every time, so the last import is the whole truth. A
    UID written twice (a forwarded link) keeps its first start and its last
    completion; a UID the ledger never sent to is kept and counted."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise CompanyReachError(
                f"{path} needs the columns {', '.join(_COLUMNS)}; missing "
                f"{', '.join(missing)} (found {', '.join(reader.fieldnames or [])})"
            )
        found: dict[str, tuple[str | None, str | None]] = {}
        for line, row in enumerate(reader, start=2):
            raw = (row["uid"] or "").strip()
            uid = compact_uid(raw) or raw
            started = _when(row["started_at"] or "", line)
            completed = _when(row["completed_at"] or "", line)
            started = started or completed  # finishing means it was started
            before = found.get(uid)
            if before:
                starts = [t for t in (before[0], started) if t]
                ends = [t for t in (before[1], completed) if t]
                started = min(starts) if starts else None
                completed = max(ends) if ends else None
            found[uid] = (started, completed)

    conn.execute("delete from responses")
    conn.executemany(
        "insert into responses (uid, started_at, completed_at, imported_at)"
        " values (?,?,?,?)",
        [(uid, s, c, now()) for uid, (s, c) in found.items()],
    )
    matched = conn.execute(
        """select count(*) from responses
            where uid in (select uid from ledger where status = 'sent')"""
    ).fetchone()[0]
    return ImportReport(rows=len(found), matched=matched)


def report_rows(
    conn: sqlite3.Connection, *, within_days: int | None = 21
) -> tuple[list[Group], int]:
    """Counts per (frame version, arm, contact kind), from each company's
    latest `sent` row, and how many responses have no `sent` row at all.
    With `within_days`, a start or completion counts only that many days
    after the mail — the plan's primary metric uses 21."""
    window = (
        ""
        if within_days is None
        else "and julianday({col}) - julianday(s.decided_at) <= ?"
    )
    params: list = [] if within_days is None else [within_days, within_days]
    rows = conn.execute(
        f"""with sent as (
                select l.uid, l.decided_at,
                       coalesce(l.frame_version, '-') as frame_version,
                       coalesce(l.arm, '-') as arm,
                       coalesce(l.contact_kind, '-') as contact_kind
                  from ledger l
                 where l.status = 'sent'
                   and l.id = (select max(id) from ledger
                                where uid = l.uid and status = 'sent'))
            select s.frame_version, s.arm, s.contact_kind,
                   count(*) as sent,
                   sum(exists (select 1 from ledger b
                                where b.uid = s.uid and b.status = 'bounced'))
                       as bounced,
                   sum(exists (select 1 from suppression x where x.key = s.uid)
                       or exists (select 1 from ledger n
                                   where n.uid = s.uid and n.status = 'never'))
                       as never,
                   sum(r.started_at is not null
                       {window.format(col="r.started_at")}) as started,
                   sum(r.completed_at is not null
                       {window.format(col="r.completed_at")}) as completed
              from sent s left join responses r on r.uid = s.uid
             group by s.frame_version, s.arm, s.contact_kind
             order by s.frame_version, s.arm, s.contact_kind""",
        params,
    ).fetchall()
    groups = [
        Group(
            frame_version=r["frame_version"],
            arm=r["arm"],
            contact_kind=r["contact_kind"],
            sent=r["sent"],
            bounced=r["bounced"] or 0,
            never=r["never"] or 0,
            started=r["started"] or 0,
            completed=r["completed"] or 0,
        )
        for r in rows
    ]
    unmatched = conn.execute(
        """select count(*) from responses
            where uid not in (select uid from ledger where status = 'sent')"""
    ).fetchone()[0]
    return groups, unmatched


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval for k successes in n: unlike p ± z·se it
    stays inside [0, 1] and is honest for small n and rates near zero."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int) -> str:
    """The rate and its Wilson 95% interval, as in `12.5% [4.2–30.1]`."""
    if n == 0:
        return "–"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.1f}% [{100 * lo:.1f}–{100 * hi:.1f}]"
