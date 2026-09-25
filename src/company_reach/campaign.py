"""Where the campaign stands: the numbers `status` prints and the front
page shows, computed in one place."""

import sqlite3
from dataclasses import dataclass

from company_reach.profile import goal_hash
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import current_criteria_hash, status_by_municipality

COLUMNS = ("pooled", "kept", "scored", "drawable", "drawn", "sent", "undecided")


@dataclass
class CampaignStatus:
    goal_hash: str
    score_version: str
    model: str
    criteria_hash: str | None
    min_score: int
    rows: list[sqlite3.Row]

    def totals(self) -> dict[str, int]:
        return {c: sum(row[c] for row in self.rows) for c in COLUMNS}


def campaign_status(
    conn: sqlite3.Connection, settings: Settings, goal: str
) -> CampaignStatus:
    version, _ = llm.load_prompt("score")
    key = goal_hash(goal)
    criteria = current_criteria_hash(conn, key)
    rows = status_by_municipality(
        conn,
        goal_hash=key,
        prompt_version=version,
        model=settings.llm_model,
        criteria_hash=criteria,
        min_score=settings.draw_min_score,
    )
    return CampaignStatus(
        key, version, settings.llm_model, criteria, settings.draw_min_score, rows
    )
