"""Pool stage 2: mark companies the rules exclude. Writes screen_reason;
NULL means kept."""

from company_reach.models import CompanyRecord
from company_reach.screen import screen_reason
from company_reach.settings import Settings
from company_reach.tools.db import connect


def screen_pool(run_id: str, *, settings: Settings) -> tuple[int, int]:
    kept = dropped = 0
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            "select uid, name, legal_form, municipality, street, postal_code, "
            "city, purpose, purpose_head from companies where import_run_id = ?",
            (run_id,),
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
