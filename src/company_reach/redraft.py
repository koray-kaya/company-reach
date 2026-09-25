"""`redraft`: draft again the review cards a run can no longer send.

A draft stops being sendable when anything it was built from changed: the
profile's `survey_url` (it links to the old survey), any other profile text
the frame writes (a closing date, a supervisor), the contact (a retry found
someone newer), or the frame itself; or when it was never checked, or
failed. Such a card is fixed from what SQLite already holds — the company,
its profile and its contact. Nothing is searched or fetched, and no page is
read again: the company was already found, and only the mail changed.

When the stored sentence still passes its rules, only the frame is stale,
and the mail is rebuilt around that sentence by code — no model call. Only
a sentence that fails its rules (or a draft from before frame@1, whose text
was a paragraph) goes back to the model, through `draft` and `check_draft`.

Only undecided send cards are touched. A decided company keeps the draft it
was decided on, and a company on the never-again list is left alone.
"""

import asyncio
import re
import sqlite3
from dataclasses import dataclass

from company_reach.errors import CompanyReachError, ProfileError
from company_reach.models import CompanyResult, Draft
from company_reach.nodes.check_draft import (
    check_draft,
    problems,
    reassemble,
    sentence_problems,
)
from company_reach.nodes.draft import draft
from company_reach.nodes.recommend import recommend
from company_reach.profile import Profile, load_profile
from company_reach.settings import Settings
from company_reach.tools.db import (
    company_by_uid,
    connect,
    contact_for,
    decision_for,
    is_suppressed,
    profile_by_uid,
    record_result,
    result_for,
    rewrite_draft,
)
from company_reach.tools.invitation import FRAME_VERSION, survey_link

_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Redrafted:
    uid: str
    outcome: str  # "sendable", "not sendable: ...", "hold: ..." or an error


_FAILED_TWICE = "the draft failed its checks twice"


def _undecided_sends(conn, run_id: str) -> list[str]:
    """Send cards, and holds whose only fault was a draft that failed its
    checks twice — its cause may be gone (a profile fixed, another model)."""
    rows = conn.execute(
        """select uid from results
            where run_id = ?
              and (recommendation = 'send'
                   or (recommendation = 'hold' and reason like ?))
            order by uid""",
        (run_id, f"{_FAILED_TWICE}%"),
    )
    return [
        r["uid"]
        for r in rows
        if decision_for(conn, r["uid"]) is None and not is_suppressed(conn, r["uid"])
    ]


def _latest_draft(conn: sqlite3.Connection, run_id: str, uid: str):
    return conn.execute(
        "select * from drafts where run_id = ? and uid = ? order by id desc limit 1",
        (run_id, uid),
    ).fetchone()


def _is_stale(conn, run_id: str, uid: str, profile: Profile) -> bool:
    """No draft, a draft of an older frame, one never checked or failed, one
    written for an older contact row, or one whose frame is not what today's
    profile and contact would build — its link included."""
    row = _latest_draft(conn, run_id, uid)
    if row is None or row["frame_version"] != FRAME_VERSION or row["problems"] != "":
        return True
    found = contact_for(conn, run_id, uid)
    if found is None or (
        row["contact_id"] is not None and row["contact_id"] != found[0]
    ):
        return True
    if _URL.findall(row["body"]) != [survey_link(profile.survey_url, uid)]:
        return True
    stored = Draft(
        subject=row["subject"],
        body=row["body"],
        model_text=row["model_text"] or "",
        link=survey_link(profile.survey_url, uid),
        mailto_fits=bool(row["mailto_fits"]),
        frame_version=row["frame_version"],
        arm=row["arm"] or "voll",
    )
    return bool(problems(stored, found[1], profile))


async def redraft_run(
    run_id: str, *, settings: Settings, uid: str | None = None
) -> list[Redrafted]:
    """Redraft the stale undecided send cards of `run_id`, or with `uid`
    that one card whether stale or not, with a new sentence from the model.
    Raises `ProfileError` before any model call when the profile cannot
    write an invitation."""
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
            uids = [u for u in open_cards if _is_stale(conn, run_id, u, profile)]
        states = []
        for u in uids:
            found = contact_for(conn, run_id, u)
            company = company_by_uid(conn, u)
            company_profile = profile_by_uid(conn, run_id, u)
            if found is None or company is None or company_profile is None:
                continue  # a send card always has all three; nothing to build on
            contact_id, contact = found
            row = _latest_draft(conn, run_id, u)
            states.append(
                {
                    "run_id": run_id,
                    "uid": u,
                    "company": company,
                    "profile": company_profile,
                    "contact": contact,
                    "contact_id": contact_id,
                    "about_me": profile.about_me,
                    "stored": row,
                }
            )

    return list(
        await asyncio.gather(
            *(_one(s, profile, settings, fresh=uid is not None) for s in states)
        )
    )


async def _one(state: dict, profile: Profile, settings: Settings, *, fresh: bool):
    uid, run_id = state["uid"], state["run_id"]
    stored = state.pop("stored")
    sentence = stored["model_text"] if stored else None
    if not fresh and sentence and not sentence_problems(sentence):
        # only the frame is stale: rebuild around the same sentence
        rebuilt, found = reassemble(sentence, state["contact"], profile, uid)
        with connect(settings.db_path) as conn:
            rewrite_draft(
                conn, stored["id"], rebuilt, found=found, contact_id=state["contact_id"]
            )
        if found:
            return Redrafted(uid, f"not sendable: {'; '.join(found)}")
        return Redrafted(uid, "sendable")
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
    with connect(settings.db_path) as conn:
        held = result_for(conn, uid, run_id=run_id)
        if held and held.recommendation == "hold":
            # the draft that held it now passes: a send card again, with the
            # reason recommend gives for this contact
            again = recommend({"site": True, **state})
            record_result(
                conn,
                CompanyResult(uid=uid, recommendation="send", reason=again["reason"]),
                run_id=run_id,
            )
    return Redrafted(uid, "sendable")
