"""The gate between what a model said and what the tool believes.

`tools/checks` holds the judgement; this node applies it and is the only
thing that writes the `profiles` table. That is deliberate and is where M5
departs from `graph.spec.yaml`, which gives the database to `extract` and
nothing to this node. Having one writer, and having it be the checked side,
means a stored profile can never contain a name the pages do not carry —
not even for the moments between two nodes of a run that then crashed.

No I/O beyond that write: everything it decides, it decides from the pages
it was handed.
"""

from typing import Any

from company_reach.models import RawProfile
from company_reach.nodes.find_site import SiteChoice
from company_reach.settings import Settings
from company_reach.tools.checks import checked
from company_reach.tools.db import connect, upsert_profile


def check_profile(state: dict[str, Any], *, settings: Settings) -> dict:
    raw: RawProfile = state["raw_profile"]
    site: SiteChoice = state["site"]

    profile = checked(
        raw,
        texts=state["page_texts"],
        site_url=site.url,
        company=state["company"].name,
    )

    with connect(settings.db_path) as conn:
        upsert_profile(conn, state["run_id"], state["uid"], profile)

    return {"profile": profile}
