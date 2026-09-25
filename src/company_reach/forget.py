"""A deletion request, honoured everywhere the tool keeps personal data.

The invitation promises it: "Ein kurzes «Nein» genügt, dann lösche ich Ihren
Namen" (frame@1, `tools/invitation.privacy`). It promises the name, never
the address, because the address is kept on the never-again list for good.
So `forget <uid|email>`:

* removes the company's contacts (and the alternatives named beside them),
  drafts, profiles and site evidence, and the person's name from the
  recommendation's reason;
* removes the pages read from the company's site, from the database and
  from the page cache;
* keeps the ledger's decisions but clears their address, because "contacted
  once, ever" has to hold after the person is forgotten;
* suppresses the uid, the address when one was given, and every address
  the company was written to (the ledger no longer holds them), for good;
* compacts the database, since SQLite keeps deleted rows in free pages until
  it is vacuumed, and a grep of `data/` must find nothing (`audit:256`).

An address is looked up wherever the tool kept one: the contact and every
address its card offered, the persons a profile names, and the ledger —
after a purge the only place left. A reply usually comes from the person's
own mailbox rather than the inbox the mail went to. An address found
nowhere is still suppressed, and the command says how to find the company:
by the `c=` UID in the survey link the reply quotes.

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
from company_reach.tools.db import connect, is_suppressed, suppress
from company_reach.tools.urls import address_key, email_domain, registered_domain

_UID = re.compile(r"^CHE[-.\s\d]+$", re.IGNORECASE)
_TEXT_FILES = {".md", ".json", ".jsonl", ".txt", ".html", ".csv"}


@dataclass
class Report:
    companies: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    rows_deleted: int = 0
    cache_files_deleted: int = 0
    still_named: list[Path] = field(default_factory=list)
    # an address no table holds and nobody suppressed before: nothing could
    # be deleted, and the company has to be found another way
    unknown: bool = False


def _uids_for(conn: sqlite3.Connection, key: str) -> list[str]:
    """The companies an address belongs to, wherever the tool kept it: the
    chosen contact and every address the card offered, the persons a
    profile names, and the ledger — which, after a purge, is the only place
    left. Exact matches only, by `address_key` (case, internationalised
    domains); a LIKE pattern read "_" as any character and forgot the
    wrong company."""
    if _UID.match(key):
        return ["CHE" + re.sub(r"\D", "", key)]
    rows = conn.execute(
        """select uid from contacts
            where address_key(email) = :key
               or exists (select 1 from json_each(contacts.addresses)
                           where address_key(json_extract(value, '$.email')) = :key)
           union
           select uid from profiles
            where exists (select 1 from json_each(profiles.profile, '$.persons')
                           where address_key(json_extract(value, '$.email')) = :key)
           union
           select uid from ledger where address_key(address) = :key""",
        {"key": address_key(key)},
    )
    return sorted(r["uid"] for r in rows)


def _sent_addresses(conn: sqlite3.Connection, uids: list[str]) -> list[str]:
    """The addresses these companies were written to. `forget` clears them
    from the ledger and keeps them on the never-again list instead, so an
    inbox that asked to be forgotten is not written to again for a sister
    company."""
    marks = ",".join("?" * len(uids))
    rows = conn.execute(
        f"""select distinct address_key(address) as address from ledger
             where uid in ({marks}) and status = 'sent' and address is not null
             order by 1""",
        uids,
    )
    return [r["address"] for r in rows]


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
    # searches: the log can name people; responses: the survey's timestamps
    # for the company — both go with everything else
    for table in ("contacts", "drafts", "profiles", "sites", "searches", "responses"):
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
            meta = json.loads(side.read_text())
            # a page kept under the URL asked for may hold the text of the
            # site it redirected to
            urls = [meta["url"], meta.get("final_url") or meta["url"]]
        except (ValueError, KeyError):
            continue
        if any(registered_domain(url) in domains for url in urls):
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
    by_address = not _UID.match(key)
    with connect(settings.db_path) as conn:
        uids = _uids_for(conn, key)
        known = bool(uids) or (by_address and is_suppressed(conn, key))
        names = _names(conn, uids) if uids else set()
        addresses = [address_key(key)] if by_address else []
        if by_address:
            names.update({key.strip().lower(), address_key(key)})
        domains = _domains(conn, uids) if uids else set()
        if uids:
            addresses += _sent_addresses(conn, uids)
            report.rows_deleted = _delete_rows(
                conn, uids, domains, reason="forgotten", clear_ledger_addresses=True
            )
        for uid in uids:
            suppress(conn, uid, reason="forgotten on request")
        report.suppressed = list(uids)
        for address in dict.fromkeys(addresses):  # in order, once each
            suppress(conn, address, reason="forgotten on request")
            report.suppressed.append(address)
    report.unknown = by_address and not known
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
                   or exists (select 1 from sites t where t.uid = s.uid)
                   -- a company whose search or site failed has only this
                   or exists (select 1 from searches x where x.uid = s.uid))
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
