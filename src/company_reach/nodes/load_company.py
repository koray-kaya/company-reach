"""The child graph's first node: the register record for one company.

A uid the graph was asked to enrich but the database does not hold is a bug
in whatever drew the batch, not a finding about a company — so it raises.
"""

from typing import Any

from company_reach.errors import CompanyReachError
from company_reach.settings import Settings
from company_reach.tools.db import company_by_uid, connect


def load_company(state: dict[str, Any], *, settings: Settings) -> dict:
    with connect(settings.db_path) as conn:
        record = company_by_uid(conn, state["uid"])
    if record is None:
        raise CompanyReachError(f"no company {state['uid']} in the database")
    return {"company": record}
