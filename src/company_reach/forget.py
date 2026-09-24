"""A deletion request, honoured everywhere the tool keeps personal data.

The invitation promises it: "wenn Sie kurz antworten, lösche ich sie und
schreibe Ihnen nicht wieder". So `forget <uid|email>`:

* removes the company's contacts (and the alternatives named beside them),
  drafts, profiles and site evidence, and the person's name from the
  recommendation's reason;
* removes the pages read from the company's site, from the database and
  from the page cache;
* keeps the ledger's decisions but clears their address, because "contacted
  once, ever" has to hold after the person is forgotten;
* suppresses the uid, and the address when one was given, for good;
* compacts the database, since SQLite keeps deleted rows in free pages until
  it is vacuumed, and a grep of `data/` must find nothing (`audit:256`).

Hand-kept research files (`data/v0`, `data/golden`) are not edited by code.
Every file that still names the person is reported, so a human can.

`purge` is the same deletion without a request: personal data of companies
nobody has touched for a year (#27, decided with Koray). It suppresses
nobody, and it leaves the ledger whole — a `sent` row keeps its address as
the record of what was sent and the key to a later deletion request.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from company_reach.models import CompanyProfile
from company_reach.settings import Settings
from company_reach.tools.db import connect, suppress
from company_reach.tools.urls import email_domain, registered_domain

_UID = re.compile(r"^CHE[-.\s\d]+$", re.IGNORECASE)
_TEXT_FILES = {".md", ".json", ".jsonl", ".txt", ".html", ".csv"}


@dataclass
class Report:
    companies: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    rows_deleted: int = 0
    cache_files_deleted: int = 0
    still_named: list[Path] = field(default_factory=list)


def _uids_for(conn: sqlite3.Connection, key: str) -> list[str]:
    if _UID.match(key):
        return ["CHE" + re.sub(r"\D", "", key)]
    email = key.strip().lower()
    rows = conn.execute(
        "select distinct uid from contacts where lower(email) = ? "
        "or lower(addresses) like ?",
        (email, f'%"{email}"%'),
    )
    return sorted(r["uid"] for r in rows)


def _names(conn: sqlite3.Connection, uids: list[str]) -> set[str]:
    """Everyone the tool named at these companies: what must not survive."""
    names: set[str] = set()
    marks = ",".join("?" * len(uids))
    for row in conn.execute(
        "select name, email, alternatives, addresses from contacts"
        f" where uid in ({marks})",
        uids,
    ):
        names.update(n for n in (row["name"], row["email"]) if n)
        names.update(a.split(",")[0] for a in json.loads(row["alternatives"] or "[]"))
        names.update(a["email"] for a in json.loads(row["addresses"] or "[]"))
    for row in conn.execute(
        f"select profile from profiles where uid in ({marks})", uids
    ):
        profile = CompanyProfile.model_validate_json(row["profile"])
        names.update(p.name for p in profile.persons)
        names.update(p.email for p in profile.persons if p.email)
    return {n for n in names if n.strip()}


_ON_SITE = ("seen", "generic", "constructed")


def _domains(conn: sqlite3.Connection, uids: list[str]) -> set[str]:
    """The company's own site, whose pages are deleted. From the site record,
    and — for runs from before it existed — from the contact: the page the
    name was found on, and the addresses on the site's own domain. Never an
    off-site address: forgetting one company must not empty a mail
    provider's pages from the cache."""
    marks = ",".join("?" * len(uids))
    urls = [
        r["url"]
        for r in conn.execute(
            f"select url from sites where uid in ({marks}) and url is not null", uids
        )
    ]
    domains = {d for u in urls if (d := registered_domain(u))}
    for row in conn.execute(
        "select source, source_url, email, email_kind, addresses from contacts"
        f" where uid in ({marks})",
        uids,
    ):
        if row["source"] == "site" and row["source_url"]:
            domains.add(registered_domain(row["source_url"]))
        if row["email"] and row["email_kind"] in _ON_SITE:
            domains.add(email_domain(row["email"]))
        for a in json.loads(row["addresses"] or "[]"):
            if a["kind"] in _ON_SITE:
                domains.add(email_domain(a["email"]))
    return {d for d in domains if d}


def _delete_rows(
    conn: sqlite3.Connection,
    uids: list[str],
    domains: set[str],
    *,
    reason: str,
    clear_ledger_addresses: bool,
) -> int:
    marks = ",".join("?" * len(uids))
    deleted = 0
    for table in ("contacts", "drafts", "profiles", "sites"):
        deleted += conn.execute(
            f"delete from {table} where uid in ({marks})", uids
        ).rowcount
    conn.execute(
        f"update results set reason = ? where uid in ({marks})", [reason, *uids]
    )
    if clear_ledger_addresses:
        conn.execute(f"update ledger set address = null where uid in ({marks})", uids)
    for row in conn.execute("select url from pages").fetchall():
        if registered_domain(row["url"]) in domains:
            deleted += conn.execute(
                "delete from pages where url = ?", (row["url"],)
            ).rowcount
    return deleted


def _delete_cache(cache: Path, domains: set[str]) -> int:
    deleted = 0
    for side in cache.glob("*.json") if cache.is_dir() else []:
        try:
            url = json.loads(side.read_text())["url"]
        except (ValueError, KeyError):
            continue
        if registered_domain(url) in domains:
            for path in (side, side.with_suffix(".html")):
                if path.exists():
                    path.unlink()
                    deleted += 1
    return deleted


def _compact(path: Path) -> None:
    """VACUUM cannot run inside a transaction, so it gets its own
    connection; the checkpoint empties the WAL file first."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _still_named(data_dir: Path, names: set[str]) -> list[Path]:
    found = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if any(n.lower() in text for n in names):
            found.append(path)
    return found


def forget(settings: Settings, key: str) -> Report:
    report = Report()
    with connect(settings.db_path) as conn:
        uids = _uids_for(conn, key)
        names = _names(conn, uids) if uids else set()
        if not _UID.match(key):
            names.add(key.strip().lower())
        domains = _domains(conn, uids) if uids else set()
        if uids:
            report.rows_deleted = _delete_rows(
                conn, uids, domains, reason="forgotten", clear_ledger_addresses=True
            )
        for uid in uids:
            suppress(conn, uid, reason="forgotten on request")
        report.suppressed = list(uids)
        if not _UID.match(key):
            suppress(conn, key, reason="forgotten on request")
            report.suppressed.append(key.strip().lower())
    report.companies = uids
    report.cache_files_deleted = _delete_cache(settings.data_dir / "cache", domains)
    _compact(settings.db_path)
    report.still_named = _still_named(settings.data_dir, names)
    return report


def stale_uids(conn: sqlite3.Connection, *, cutoff: str) -> list[str]:
    """Companies drawn before `cutoff` with no decision since, that still
    have personal data to purge."""
    rows = conn.execute(
        """select s.uid from seen s
            where s.drawn_at < ?
              and not exists (select 1 from ledger l
                               where l.uid = s.uid and l.decided_at >= ?)
              and (exists (select 1 from contacts c where c.uid = s.uid)
                   or exists (select 1 from profiles p where p.uid = s.uid)
                   or exists (select 1 from sites t where t.uid = s.uid))
            order by s.uid""",
        (cutoff, cutoff),
    )
    return [r["uid"] for r in rows]


def purge(
    settings: Settings, *, older_than_days: int, today: str | None = None
) -> Report:
    report = Report()
    day = date.fromisoformat(today) if today else date.today()
    cutoff = (day - timedelta(days=older_than_days)).isoformat()
    with connect(settings.db_path) as conn:
        uids = stale_uids(conn, cutoff=cutoff)
        if not uids:
            return report
        domains = _domains(conn, uids)
        report.rows_deleted = _delete_rows(
            conn, uids, domains, reason="purged", clear_ledger_addresses=False
        )
    report.companies = uids
    report.cache_files_deleted = _delete_cache(settings.data_dir / "cache", domains)
    _compact(settings.db_path)
    return report
