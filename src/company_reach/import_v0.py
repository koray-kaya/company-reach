"""The prototype's decisions, brought into the ledger once.

"A company is contacted once, ever" (`LEARNINGS.md` §6) has to hold across
the rewrite: the v0 prototype drew and reviewed 22 companies, and without
this import the new tool could draw them again (`audit-2026-09-19.md:304`).

v0 sent no mail — confirmed by Koray on 2026-09-24. Its log records skip,
hold and "pending" (a recommended send nobody acted on). So:

* every company in `seen.json` goes into `seen` under run `v0`, and is
  never drawn again;
* a `skip` in `outreach.md` becomes a `skipped` ledger row;
* a hold or a pending one gets no ledger row, because nobody decided.

Two files, because each holds half: `seen.json` knows every company and its
batch, `outreach.md` knows the decisions and their dates. A decision for a
company the seen file does not know means the two disagree, and the import
stops rather than guess which one is right.
"""

import json
import re
import sqlite3
from pathlib import Path

from company_reach.tools.db import record_decision

RUN_ID = "v0"
_ROW = re.compile(r"^\| (\d{4}-\d{2}-\d{2}) \| (CHE[-.\d]+) \|")
_DIGITS = re.compile(r"\D")


def _decisions(outreach: str) -> dict[str, tuple[str, str]]:
    """uid -> (date, decision), from the log's table rows."""
    found: dict[str, tuple[str, str]] = {}
    for line in outreach.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        uid = "CHE" + _DIGITS.sub("", match.group(2))
        found[uid] = (match.group(1), cells[4])
    return found


def import_v0(conn: sqlite3.Connection, v0_dir: Path) -> tuple[int, int]:
    """Returns (seen rows added, ledger rows added). Running it again adds
    nothing: `seen` is insert-or-ignore, and a company that already has a v0
    ledger row gets no second one."""
    companies = json.loads((v0_dir / "seen.json").read_text())["companies"]
    decisions = _decisions((v0_dir / "outreach.md").read_text())
    unknown = sorted(set(decisions) - set(companies))
    if unknown:
        raise ValueError(
            f"outreach.md decides about companies seen.json does not know: {unknown}"
        )

    seen_added = ledger_added = 0
    for uid, company in companies.items():
        date = decisions.get(uid, ("", ""))[0]
        drawn_at = f"{date}T00:00:00+00:00" if date else "1970-01-01T00:00:00+00:00"
        # insert-or-ignore: a company a later run drew keeps that run's row
        cur = conn.execute(
            "INSERT OR IGNORE INTO seen (uid, run_id, batch_no, drawn_at) "
            "VALUES (?,?,?,?)",
            (uid, RUN_ID, company["batch"], drawn_at),
        )
        seen_added += cur.rowcount

    for uid, (date, decision) in decisions.items():
        if decision != "skip":
            continue
        already = conn.execute(
            "select 1 from ledger where uid = ? and run_id = ?", (uid, RUN_ID)
        ).fetchone()
        if already:
            continue
        record_decision(
            conn,
            uid,
            "skipped",
            run_id=RUN_ID,
            note="v0",
            decided_at=f"{date}T00:00:00+00:00",
        )
        ledger_added += 1
    return seen_added, ledger_added
