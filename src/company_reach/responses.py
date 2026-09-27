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

Delivered is sent minus bounced, per company: each company counts by its
latest mail, so a bounce followed by a mail to another address is
delivered. A send taken back as `not_sent` was never a mail and does not
count at all. "Never" counts sent companies that are now on the never-again
list — a «Nein» reply ends in `forget`.

`refresh_from_survey` asks the survey itself (its `/api/admin/tags`,
interview-form #24) and stores the answer the same way; the Contacts page
does this each time it opens. Personal codes (`P-…`, `invites.py`) are
stored and matched like UIDs.
"""

import csv
import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from company_reach.errors import CompanyReachError
from company_reach.invites import code_in
from company_reach.tools.db import NEVER_LEFT, UNDONE, now
from company_reach.tools.invitation import compact_uid

# the tag's column: company-reach's own name, and the survey's export's
_TAG_COLUMNS = ("uid", "company_uid")
_TIME_COLUMNS = ("started_at", "completed_at")
TAGS_PATH = "/api/admin/tags"
# companies a mail left for: a sent row not taken back as never sent
_MAILED_UIDS = (
    f"(select uid from ledger where status = 'sent' and id not in {NEVER_LEFT})"
)
_MATCHED = f"(uid in {_MAILED_UIDS} or uid in (select code from invites))"


@dataclass(frozen=True)
class ImportReport:
    rows: int  # distinct UIDs in the export
    matched: int  # of them, with a 'sent' row in the ledger


class SurveyUnreachable(CompanyReachError):
    """The survey could not be asked. Nothing was stored, so the answers
    shown stay the last ones fetched: an outage must never read as
    "nobody answered"."""


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


def _when(value: str, where: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError as e:
        raise CompanyReachError(f"{where}: {value!r} is not an ISO date or time") from e


def _tag(raw: str) -> str:
    """The key a response is stored under: a UID compacted, a personal
    code in upper case, anything else as it came."""
    raw = raw.strip()
    return compact_uid(raw) or code_in(raw) or raw


def store_responses(
    conn: sqlite3.Connection, rows: Iterable[tuple[str, str | None, str | None]]
) -> ImportReport:
    """Replace the table with these (tag, started, completed) rows. The
    survey hands over everything so far every time, so the last set is the
    whole truth. A tag written twice (a forwarded link) keeps its first
    start and its last completion; a tag nobody here knows is kept and
    counted; a response without a tag names no invitation and is left out."""
    found: dict[str, tuple[str | None, str | None]] = {}
    for raw, started, completed in rows:
        uid = _tag(raw)
        if not uid:
            continue
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
        f"select count(*) from responses where {_MATCHED}"
    ).fetchone()[0]
    return ImportReport(rows=len(found), matched=matched)


def import_responses(conn: sqlite3.Connection, path: Path) -> ImportReport:
    """The survey's CSV export at `path`, stored by `store_responses`."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        tag = next((c for c in _TAG_COLUMNS if c in fields), None)
        missing = [c for c in _TIME_COLUMNS if c not in fields]
        if tag is None:
            missing.insert(0, "uid (or company_uid)")
        if missing:
            raise CompanyReachError(
                f"{path} needs the columns uid, started_at, completed_at; missing "
                f"{', '.join(missing)} (found {', '.join(fields)})"
            )
        rows = [
            (
                row[tag] or "",
                _when(row["started_at"] or "", f"line {n}"),
                _when(row["completed_at"] or "", f"line {n}"),
            )
            for n, row in enumerate(reader, start=2)
        ]
    return store_responses(conn, rows)


def fetch_tags(
    survey_url: str, password: str
) -> list[tuple[str, str | None, str | None]]:
    """Every response's tag and times, from the survey's admin endpoint at
    the survey's root. Raises SurveyUnreachable, and returns nothing, when
    the survey cannot be asked or answers something else."""
    url = urlsplit(survey_url)._replace(path=TAGS_PATH, query="", fragment="").geturl()
    try:
        response = httpx.get(url, auth=("company-reach", password), timeout=5.0)
    except httpx.HTTPError as error:
        raise SurveyUnreachable(
            f"Could not reach the survey ({type(error).__name__})."
        ) from error
    if response.status_code == 401:
        raise SurveyUnreachable(
            "The survey refused the password: FORM_ADMIN_PASSWORD must be"
            " the survey's ADMIN_PASSWORD."
        )
    if response.status_code != 200:
        raise SurveyUnreachable(
            f"The survey answered {response.status_code} at {TAGS_PATH}."
        )
    try:
        return [
            (
                str(item["tag"]),
                _when(item.get("started_at") or "", f"tag {n}"),
                _when(item.get("completed_at") or "", f"tag {n}"),
            )
            for n, item in enumerate(response.json()["tags"], start=1)
        ]
    except (ValueError, KeyError, TypeError, CompanyReachError) as error:
        raise SurveyUnreachable(
            f"The survey's answer was not a list of tags ({error})."
        ) from error


def refresh_from_survey(
    conn: sqlite3.Connection, survey_url: str, password: str
) -> ImportReport:
    """Ask first, store after: a failed fetch leaves the table as it was."""
    return store_responses(conn, fetch_tags(survey_url, password))


def report_rows(
    conn: sqlite3.Connection, *, within_days: int | None = 21
) -> tuple[list[Group], int]:
    """Counts per (frame version, arm, contact kind), from each company's
    latest mail — its latest `sent` row not taken back as never sent — and
    how many responses have no such row at all. With `within_days`, a start
    or completion counts only that many days after the mail — the plan's
    primary metric uses 21."""
    # whole days from the day of the mail: 0 is the day it was sent, and a
    # start before it (the owner trying the link, an older forward) is no
    # answer to it
    window = (
        ""
        if within_days is None
        else "and julianday(date({col})) - julianday(date(s.decided_at))"
        " between 0 and ?"
    )
    params: list = [] if within_days is None else [within_days, within_days]
    rows = conn.execute(
        f"""with mails as (
                select * from ledger
                 where status = 'sent' and id not in {NEVER_LEFT}),
            sent as (
                select l.id, l.uid, l.decided_at,
                       coalesce(l.frame_version, '-') as frame_version,
                       coalesce(l.arm, '-') as arm,
                       coalesce(l.contact_kind, '-') as contact_kind
                  from mails l
                 where l.id = (select max(id) from mails where uid = l.uid))
            select s.frame_version, s.arm, s.contact_kind,
                   count(*) as sent,
                   sum(exists (select 1 from ledger b
                                where b.reverses = s.id and b.status = 'bounced'
                                  and b.id not in {UNDONE}))
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
        f"select count(*) from responses where not {_MATCHED}"
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
