"""A deletion request, honoured everywhere the tool keeps personal data.

The invitation promises it: "Ein kurzes «Nein» genügt, dann lösche ich Ihren
Namen" (frame@1, `tools/invitation.privacy`). It promises the name, never
the address, because the address is kept on the never-again list for good.
So `forget <uid|email|survey link>` (a link's `c=` UID; a UID's check
digit is checked, and anything else is refused before anything happens):

* removes the company's contacts (and the alternatives named beside them),
  drafts, profiles and site evidence, and the person's name from the
  recommendation's reason;
* removes the pages read from the company's site, from the database and
  from the page cache;
* keeps the ledger's decisions but clears their address and a sent row's
  subject (it can name the person), because "contacted once, ever" has to
  hold after the person is forgotten;
* suppresses the uid, the address when one was given, and every address
  the company was written to (the ledger no longer holds them), for good;
* compacts the database, since SQLite keeps deleted rows in free pages until
  it is vacuumed, and a grep of `data/` must find nothing (`audit:256`).

An address is looked up wherever the tool kept one: the contact and every
address its card offered, the persons a profile names, and the ledger —
after a purge the only place left. A reply usually comes from the person's
own mailbox rather than the inbox the mail went to. An address found
nowhere — and a UID no table holds — is still suppressed, and the command
says nothing was deleted and how to find the company: by the `c=` UID in
the survey link the reply quotes.

Hand-kept research files (`data/v0`, `data/golden`) are not edited by code,
and neither are copies: a database backup under `data/`, a log, a
command's output. Every file under `data/` but code, binaries and tool
folders is searched, block by block, never whole; each that still names
the person is reported, so a human can edit or delete it, and each that
cannot be searched — compressed or unreadable — is reported as such.

`purge` is the same deletion without a request: personal data of companies
nobody has touched for a year (#27, decided with Koray) — drawn a year ago,
or never drawn (`enrich --uid`, an evaluation) and last written a year ago.
It suppresses nobody, and it leaves the ledger whole but for a sent row's
subject — a `sent` row keeps its address as the record of what was sent and
the key to a later deletion request.
"""

import codecs
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from company_reach.errors import CompanyReachError
from company_reach.models import CompanyProfile
from company_reach.settings import Settings
from company_reach.tools.db import connect, suppress, suppression_for
from company_reach.tools.uid import is_valid_uid
from company_reach.tools.urls import address_key, email_domain, registered_domain

_UID = re.compile(r"^CHE[-.\s\d]+$", re.IGNORECASE)
# Every file under data/ is searched but these: tool folders, code, and
# binaries that hold no text. An ending nobody listed — a command's .out, a
# hand-made .bak — is exactly where a name survives unnoticed.
_SKIPPED_DIRS = {
    *(".git", ".venv", "venv", "node_modules"),
    *("__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"),
}
_SKIPPED = {
    *(".py", ".pyc", ".pyo", ".js", ".css", ".map", ".so", ".dylib", ".dll"),
    *(".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"),
    *(".woff", ".woff2", ".ttf", ".otf", ".db-shm"),
}
# Text inside these is compressed, so a byte search cannot see a name: they
# are listed as not searched, for a human to open.
_COMPRESSED = {
    *(".gz", ".tgz", ".zip", ".bz2", ".xz", ".zst", ".7z", ".rar", ".pdf"),
    *(".docx", ".xlsx", ".pptx", ".odt", ".ods"),
}
_BLOCK = 1 << 20  # bytes read at a time
_LIVE = ("-wal", "-shm")  # the live database's own companions
_NO_COMPANY = "forgotten on request; no company found"
# every table that holds a row about a company, by its uid
_UID_TABLES = (
    *("companies", "scores", "seen", "results", "ledger", "contacts"),
    *("profiles", "drafts", "sites", "searches", "responses"),
)


@dataclass
class Report:
    key: str = ""  # what the request named, as parsed: a UID or an address
    by_uid: bool = False
    companies: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    rows_deleted: int = 0
    cache_files_deleted: int = 0
    still_named: list[Path] = field(default_factory=list)
    # compressed or unreadable: it may name the person, and nobody looked
    not_searched: list[Path] = field(default_factory=list)
    # a key no company holds: nothing could be deleted, and the company has
    # to be found another way — however often the key is forgotten
    unknown: bool = False
    # the key was on the never-again list before: "since <date> (<reason>)"
    already: str | None = None


def parse_key(key: str) -> tuple[bool, str]:
    """What a deletion request names: `(True, "CHE…")` for a UID — typed,
    or read from the `c=` of a pasted survey link — and `(False, address)`
    for an e-mail address. Anything else raises: a mistyped UID or a line
    of text must not become a never-again key and a reported success."""
    text = key.strip()
    from_link = parse_qs(urlsplit(text).query).get("c", [""])[0].strip()
    if from_link:
        text = from_link
    if _UID.match(text):
        uid = "CHE" + re.sub(r"\D", "", text)
        if not is_valid_uid(uid):
            raise CompanyReachError(
                f"{text} is not a UID: its check digit does not match. Nothing"
                " was forgotten; copy the UID again from the survey link (c=CHE…)."
            )
        return True, uid
    if not from_link and "@" in text and not any(c.isspace() for c in text):
        return False, text
    raise CompanyReachError(
        f"{key.strip()!r} is neither a UID (CHE…), an e-mail address nor a survey"
        " link with ?c=CHE…. Nothing was forgotten."
    )


def _uid_known(conn: sqlite3.Connection, uid: str) -> bool:
    """Whether any table holds the company — a UID nothing holds is most
    likely mistyped, and forgetting it deletes nothing."""
    return any(
        conn.execute(f"select 1 from {table} where uid = ? limit 1", (uid,)).fetchone()
        for table in _UID_TABLES
    )


def _uids_for(conn: sqlite3.Connection, by_uid: bool, key: str) -> list[str]:
    """The company a UID names, if the tool holds it, or the companies an
    address belongs to, wherever the tool kept it: the chosen contact and
    every address the card offered, the persons a profile names, and the
    ledger — which, after a purge, is the only place left. Exact matches
    only, by `address_key` (case, internationalised domains); a LIKE
    pattern read "_" as any character and forgot the wrong company."""
    if by_uid:
        return [key] if _uid_known(conn, key) else []
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


def _mailed_addresses(conn: sqlite3.Connection, uids: list[str]) -> list[str]:
    """The addresses these companies were written to, bounced ones too.
    `forget` clears them from the ledger and keeps them on the never-again
    list instead, so an inbox that asked to be forgotten is not written to
    again for a sister company. They are searched for under `data/` as
    well: after a purge they are all that is left to look for."""
    marks = ",".join("?" * len(uids))
    rows = conn.execute(
        f"""select distinct address_key(address) as address from ledger
             where uid in ({marks}) and status in ('sent', 'bounced')
               and address is not null
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
    # a sent row's subject can name the person ("Für Frau Muster: …"); its
    # body hash, versions and kind of contact stay
    conn.execute(f"update ledger set subject = null where uid in ({marks})", uids)
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


def _names_in(path: Path, needles: list[str]) -> bool:
    """Whether the file holds any of `needles` (lower case), read block by
    block: a database copy can be gigabytes, and is never read whole. The
    decoder keeps a character split between two blocks, and the tail of
    each block is searched again with the next, so a name split between
    them is found too."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    keep = max(len(n) for n in needles) - 1
    tail = ""
    with path.open("rb") as f:
        while block := f.read(_BLOCK):
            text = tail + decoder.decode(block).lower()
            if any(n in text for n in needles):
                return True
            tail = text[-keep:] if keep else ""
    return False


def _files(data_dir: Path) -> list[Path]:
    """Every file under `data_dir`, tool folders pruned before they are
    walked (a virtualenv holds thousands of files and no person)."""
    found = []
    for root, dirs, files in data_dir.walk():
        dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_DIRS)
        found.extend(root / f for f in files)
    return sorted(found)


def _still_named(
    data_dir: Path, names: set[str], *, db_path: Path
) -> tuple[list[Path], list[Path]]:
    """The files under `data/` the tool did not clean that still name
    someone — hand-kept notes, and copies: a database backup with its WAL,
    a log, a command's output — and the files it could not search, being
    compressed or unreadable. The live database, just vacuumed, is left
    out: its never-again list keeps addresses on purpose."""
    needles = sorted({n.lower() for n in names})
    if not needles:
        return [], []
    live = {db_path.resolve(), *(Path(f"{db_path}{s}").resolve() for s in _LIVE)}
    named, not_searched = [], []
    for path in _files(data_dir):
        if path.suffix.lower() in _SKIPPED or path.resolve() in live:
            continue
        if path.suffix.lower() in _COMPRESSED:
            not_searched.append(path)
            continue
        try:
            if _names_in(path, needles):
                named.append(path)
        except OSError:
            not_searched.append(path)
    return named, not_searched


def forget(settings: Settings, key: str) -> Report:
    """Raises `CompanyReachError`, before touching anything, when `key` is
    neither a valid UID, an address nor a survey link carrying one."""
    by_uid, key = parse_key(key)
    report = Report(key=key if by_uid else address_key(key), by_uid=by_uid)
    with connect(settings.db_path) as conn:
        uids = _uids_for(conn, by_uid, key)
        if before := suppression_for(conn, key):
            report.already = f"since {before['added_at'][:10]} ({before['reason']})"
        names = _names(conn, uids) if uids else set()
        # the key itself goes on the list when it is an address, or a UID
        # no table holds; a company found is suppressed by its uid below
        keys = [] if by_uid and uids else [report.key]
        if not by_uid:
            names.update({key.lower(), address_key(key)})
        domains = _domains(conn, uids) if uids else set()
        if uids:
            mailed = _mailed_addresses(conn, uids)
            keys += mailed
            names.update(mailed)
            report.rows_deleted = _delete_rows(
                conn, uids, domains, reason="forgotten", clear_ledger_addresses=True
            )
        for uid in uids:
            suppress(conn, uid, reason="forgotten on request")
        report.suppressed = list(uids)
        # a key no company holds keeps saying so: a second request with it
        # must not read the first one's row as a company found
        reason = "forgotten on request" if uids else _NO_COMPANY
        for other in dict.fromkeys(keys):  # in order, once each
            suppress(conn, other, reason=reason)
            report.suppressed.append(other)
    report.unknown = not uids
    report.companies = uids
    report.cache_files_deleted = _delete_cache(settings.data_dir / "cache", domains)
    _compact(settings.db_path)
    report.still_named, report.not_searched = _still_named(
        settings.data_dir, names, db_path=settings.db_path
    )
    return report


def stale_uids(conn: sqlite3.Connection, *, cutoff: str) -> list[str]:
    """Companies that still hold personal data and that nobody touched
    since `cutoff`: no decision since, and drawn before it — or, never
    drawn at all (`enrich --uid`, an evaluation: K8), last written before
    it. Such a company's age is its newest dated row, a search or a draft;
    one with no dated row at all counts as old."""
    rows = conn.execute(
        """with held as (
               select uid from contacts union select uid from profiles
               union select uid from sites union select uid from drafts
               -- a company whose search or site failed has only this
               union select uid from searches)
           select h.uid from held h left join seen s on s.uid = h.uid
            where not exists (select 1 from ledger l
                               where l.uid = h.uid and l.decided_at >= :cutoff)
              and case when s.uid is not null then s.drawn_at < :cutoff
                       else max(coalesce((select max(created_at) from drafts d
                                           where d.uid = h.uid), ''),
                                coalesce((select max(at) from searches x
                                           where x.uid = h.uid), '')) < :cutoff
                  end
            order by h.uid""",
        {"cutoff": cutoff},
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
