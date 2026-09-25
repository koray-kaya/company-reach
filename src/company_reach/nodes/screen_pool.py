"""Pool stage 2, for when the rules change: apply `screen.py` again to every
company. An import already screens what it stores (`upsert_companies`), so
this is not a step of every pool; it keeps old rows in line with new rules.
Writes screen_reason; NULL means kept."""

from company_reach.models import CompanyRecord
from company_reach.screen import screen_reason
from company_reach.settings import Settings
from company_reach.tools.db import connect


def screen_pool(*, settings: Settings) -> tuple[int, int]:
    """(kept, dropped) over every company in the database. (0, 0) means the
    database holds none, which the caller must not report as done."""
    kept = dropped = 0
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "select uid, name, legal_form, municipality, street, postal_code, "
            "city, purpose, purpose_head from companies"
        ).fetchall()
        for row in rows:
            reason = screen_reason(CompanyRecord(**dict(row)))
            conn.execute(
                "update companies set screen_reason = ? where uid = ?",
                (reason, row["uid"]),
            )
            if reason is None:
                kept += 1
            else:
                dropped += 1
    return kept, dropped
