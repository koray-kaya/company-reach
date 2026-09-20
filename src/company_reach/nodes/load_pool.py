"""Pool stage 1: LINDAS → companies table."""

from company_reach.settings import Settings
from company_reach.tools.db import connect, upsert_companies
from company_reach.tools.lindas import fetch_companies


def load_pool(municipality: str, run_id: str, *, settings: Settings) -> int:
    records = fetch_companies(municipality, url=settings.lindas_url)
    with connect(settings.db_path) as conn:
        return upsert_companies(conn, records, run_id)
