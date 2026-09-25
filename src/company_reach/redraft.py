"""`redraft`: draft again the review cards a run can no longer send.

A draft stops being sendable when the profile's `survey_url` changes (it
links to the old survey), when the frame changes (`frame_version`), or when
it was never checked or failed. Such a card is fixed from what SQLite
already holds — the company, its profile and its contact — through `draft`
and `check_draft` only. Nothing is searched or fetched, and no page is read
again: the company was already found, and only the mail changed.

Only undecided send cards are touched. A decided company keeps the draft it
was decided on, and a company on the never-again list is left alone.
"""

import asyncio
import re
from dataclasses import dataclass

from company_reach.errors import CompanyReachError, ProfileError
from company_reach.models import CompanyResult
from company_reach.nodes.check_draft import check_draft
from company_reach.nodes.draft import draft
from company_reach.profile import load_profile
from company_reach.settings import Settings
from company_reach.tools.db import (
    company_by_uid,
    connect,
    contact_for,
    decision_for,
    is_suppressed,
    profile_by_uid,
    record_result,
)
from company_reach.tools.invitation import FRAME_VERSION, survey_link

_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Redrafted:
    uid: str
    outcome: str  # "sendable", "hold: <why>" or "<kind> error: <what>"


def _undecided_sends(conn, run_id: str) -> list[str]:
    rows = conn.execute(
        "select uid from results where run_id = ? and recommendation = 'send'"
        " order by uid",
        (run_id,),
    )
    return [
        r["uid"]
        for r in rows
        if decision_for(conn, r["uid"]) is None and not is_suppressed(conn, r["uid"])
    ]


def _is_stale(conn, run_id: str, uid: str, survey_url: str) -> bool:
    """No draft, a draft of an older frame, one never checked or failed, or
    one whose link is not the link the profile would give today."""
    row = conn.execute(
        "select body, frame_version, problems from drafts"
        " where run_id = ? and uid = ? order by id desc limit 1",
        (run_id, uid),
    ).fetchone()
    if row is None or row["frame_version"] != FRAME_VERSION or row["problems"] != "":
        return True
    return _URL.findall(row["body"]) != [survey_link(survey_url, uid)]


async def redraft_run(
    run_id: str, *, settings: Settings, uid: str | None = None
) -> list[Redrafted]:
    """Redraft the stale undecided send cards of `run_id`, or with `uid`
    that one card whether stale or not. Raises `ProfileError` before any
    model call when the profile cannot write an invitation."""
    profile = load_profile(settings.profile_path)
    if gaps := profile.drafting_gaps():
        raise ProfileError(
            f"{settings.profile_path} is missing what every invitation needs: "
            f"{', '.join(gaps)}"
        )

    with connect(settings.db_path) as conn:
        open_cards = _undecided_sends(conn, run_id)
        if uid is not None:
            if uid not in open_cards:
                raise CompanyReachError(
                    f"{uid} is not an undecided send card in run {run_id}"
                )
            uids = [uid]
        else:
            uids = [
                u for u in open_cards if _is_stale(conn, run_id, u, profile.survey_url)
            ]
        states = []
        for u in uids:
            found = contact_for(conn, run_id, u)
            company = company_by_uid(conn, u)
            company_profile = profile_by_uid(conn, run_id, u)
            if found is None or company is None or company_profile is None:
                continue  # a send card always has all three; nothing to build on
            contact_id, contact = found
            states.append(
                {
                    "run_id": run_id,
                    "uid": u,
                    "company": company,
                    "profile": company_profile,
                    "contact": contact,
                    "contact_id": contact_id,
                    "about_me": profile.about_me,
                }
            )

    return list(await asyncio.gather(*(_one(s, settings) for s in states)))


async def _one(state: dict, settings: Settings) -> Redrafted:
    uid, run_id = state["uid"], state["run_id"]
    try:
        state |= await draft(state, settings=settings)
        state |= await check_draft(state, settings=settings)
    except CompanyReachError as error:
        # the card stays unsendable: its old draft is stale, or check_draft
        # deleted the one that failed; `redraft` again when the cause is gone
        return Redrafted(uid, f"{type(error).__name__}: {error}")
    if state.get("recommendation") == "hold":
        with connect(settings.db_path) as conn:
            record_result(
                conn,
                CompanyResult(uid=uid, recommendation="hold", reason=state["reason"]),
                run_id=run_id,
            )
        return Redrafted(uid, f"hold: {state['reason']}")
    return Redrafted(uid, "sendable")
