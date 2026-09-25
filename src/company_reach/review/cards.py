"""One card per company of a run: everything the page shows, read from
SQLite, and whether the company may be sent.

The page runs outside the graph, so this is the only place that knows how
the child's rows fit together. Nothing here writes.

Send is refused, with the reason the card shows, when any of these holds
(M7 open points 1 and 2):

* the company is already decided, or on the never-again list;
* there is no draft — a hold or a skip was never drafted, and a draft that
  failed its checks twice was deleted;
* ethics approval is not recorded (`SENDING_APPROVED` in `.env`);
* the draft's survey link is a placeholder, or points somewhere other than
  the profile's current `survey_url`.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from company_reach.models import CompanyProfile, Contact, dotted_uid
from company_reach.tools.db import (
    company_by_uid,
    decision_for,
    is_suppressed,
    search_log,
)
from company_reach.tools.mailto import build

_LEGAL_FORMS = {"0106": "AG", "0107": "GmbH"}
_TIERS = {
    "uid": "UID matches the register",
    "address": "address matches, no UID on the site",
    "model": "model choice",
}
_ORDER = {"send": 0, "hold": 1, "skip": 2}
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class DraftView:
    id: int
    subject: str
    body: str
    mailto_fits: bool
    prompt_version: str
    model: str

    @property
    def link(self) -> str | None:
        found = _URL.findall(self.body)
        return found[0] if found else None


@dataclass(frozen=True)
class Card:
    uid: str
    uid_dotted: str
    name: str
    legal_form: str
    seat: str
    what: str
    site_url: str | None
    evidence: str | None
    evidence_url: str | None
    evidence_label: str | None
    queries: list[str]
    searches: list[str]
    recommendation: str | None
    reason: str
    contact: Contact | None
    draft: DraftView | None
    link_length: int
    decision: str | None
    send_block: str | None


def safe_url(url: str | None) -> str | None:
    """Only http(s) is ever rendered as a link (`audit:147`): a search
    result or a page can carry `javascript:` or `data:`."""
    if not url:
        return None
    return url if urlsplit(url).scheme in ("http", "https") else None


def _search_line(row: sqlite3.Row) -> str:
    """`"Muster Metallbau" Musterstadt — searxng · 0 results · down: brave`"""
    if row["error"]:
        outcome = f"error: {row['error']}"
    else:
        n = row["result_count"] or 0
        outcome = "1 result" if n == 1 else f"{n} results"
    down = json.loads(row["unresponsive"] or "[]")
    if down:
        outcome += f" · down: {', '.join(down)}"
    return f"{row['query']} — {row['provider']} · {outcome}"


def _contact(conn: sqlite3.Connection, run_id: str, uid: str) -> Contact | None:
    row = conn.execute(
        "select * from contacts where run_id = ? and uid = ? order by id desc limit 1",
        (run_id, uid),
    ).fetchone()
    if row is None:
        return None
    addresses = json.loads(row["addresses"] or "[]")
    if not addresses and row["email"] and row["email_kind"]:
        # a run from before M7 stored only the chosen address
        addresses = [{"email": row["email"], "kind": row["email_kind"]}]
    return Contact(
        name=row["name"],
        role=row["role"],
        email=row["email"],
        email_kind=row["email_kind"],
        source=row["source"],
        source_url=row["source_url"],
        source_date=row["source_date"],
        linkedin_lead=row["linkedin_lead"],
        alternatives=json.loads(row["alternatives"] or "[]"),
        addresses=addresses,
    )


def _draft(conn: sqlite3.Connection, run_id: str, uid: str) -> DraftView | None:
    row = conn.execute(
        "select * from drafts where run_id = ? and uid = ? order by id desc limit 1",
        (run_id, uid),
    ).fetchone()
    if row is None:
        return None
    return DraftView(
        id=row["id"],
        subject=row["subject"],
        body=row["body"],
        mailto_fits=bool(row["mailto_fits"]),
        prompt_version=row["prompt_version"],
        model=row["model"],
    )


def _send_block(
    *,
    draft: DraftView | None,
    reason: str,
    decision: str | None,
    suppressed: bool,
    survey_url: str,
    sending_approved: bool,
) -> str | None:
    if decision is not None:
        return f"Already decided: {decision}."
    if suppressed:
        return "This company is on the never-again list."
    if draft is None:
        return f"No draft to send — {reason}."
    if not sending_approved:
        return (
            "Sending is locked until ethics approval is recorded "
            "(SENDING_APPROVED=true in .env)."
        )
    link = draft.link or ""
    host = urlsplit(link).hostname or ""
    if host == "example" or host.endswith(".example"):
        return f"The survey link is a placeholder ({host}); set survey_url and redraft."
    if not survey_url or not link.startswith(survey_url.rstrip("/")):
        return (
            "The draft links to a survey other than the profile's survey_url; redraft."
        )
    return None


def load_cards(
    conn: sqlite3.Connection, run_id: str, *, survey_url: str, sending_approved: bool
) -> list[Card]:
    """Every company with a result in the run: send, then hold, then skip,
    then the ones that errored; inside a group, the order they were drawn."""
    rows = conn.execute(
        """select r.uid, r.recommendation, r.reason, r.error_kind, r.error_text,
                  coalesce(s.batch_no, 0) as batch_no
             from results r left join seen s on s.uid = r.uid
            where r.run_id = ?""",
        (run_id,),
    ).fetchall()
    rows = sorted(
        rows,
        key=lambda r: (_ORDER.get(r["recommendation"], 3), r["batch_no"], r["uid"]),
    )

    cards: list[Card] = []
    for r in rows:
        uid = r["uid"]
        record = company_by_uid(conn, uid)
        site = conn.execute(
            "select * from sites where run_id = ? and uid = ?", (run_id, uid)
        ).fetchone()
        profile_row = conn.execute(
            "select profile from profiles where run_id = ? and uid = ?", (run_id, uid)
        ).fetchone()
        profile = (
            CompanyProfile.model_validate_json(profile_row["profile"])
            if profile_row
            else None
        )
        contact = _contact(conn, run_id, uid)
        draft = _draft(conn, run_id, uid)
        latest = decision_for(conn, uid)
        decision = latest["status"] if latest else None
        reason = r["reason"] or (
            f"{r['error_kind']} error: {r['error_text']}" if r["error_kind"] else ""
        )
        to = contact.email if contact and contact.email else ""
        cards.append(
            Card(
                uid=uid,
                uid_dotted=dotted_uid(uid),
                name=record.name if record else uid,
                legal_form=_LEGAL_FORMS.get(record.legal_form, "") if record else "",
                seat=(record.city or record.municipality) if record else "",
                what=profile.description
                if profile
                else (record.purpose_head if record else ""),
                site_url=safe_url(site["url"]) if site else None,
                evidence=site["evidence"] if site else None,
                evidence_url=safe_url(site["evidence_url"]) if site else None,
                evidence_label=_TIERS.get(site["tier"])
                if site and site["tier"]
                else None,
                queries=json.loads(site["queries"] or "[]") if site else [],
                searches=[_search_line(row) for row in search_log(conn, run_id, uid)],
                recommendation=r["recommendation"],
                reason=reason,
                contact=contact,
                draft=draft,
                link_length=build(to, draft.subject, draft.body).length if draft else 0,
                decision=decision,
                send_block=_send_block(
                    draft=draft,
                    reason=reason,
                    decision=decision,
                    suppressed=is_suppressed(conn, uid),
                    survey_url=survey_url,
                    sending_approved=sending_approved,
                ),
            )
        )
    return cards


def first_undecided(cards: list[Card]) -> int:
    """Where `/review/{run}` opens: the first card nobody decided about, or
    the last card when every one is decided."""
    for i, card in enumerate(cards):
        if card.decision is None:
            return i
    return max(len(cards) - 1, 0)
