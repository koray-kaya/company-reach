"""One card per company of a run: everything the page shows, read from
SQLite, and whether the company may be sent.

The page runs outside the graph, so this is the only place that knows how
the child's rows fit together. Nothing here writes.

Send is refused, with the reason the card shows, when any of these holds
(M7 open points 1 and 2):

* the company is already decided, or on the never-again list;
* there is no draft — a hold or a skip was never drafted, and a draft that
  failed its checks twice was deleted;
* the draft was written with an older frame, was never checked, or failed
  its checks: Send rests on the latest check's outcome, never on a draft
  row existing;
* the draft no longer matches today's profile or contact — a new closing
  date, a supervisor taken out, a newer contact found by a retry: the frame
  is checked again here, in code, on every load;
* no address the card offers may be written to: each is on the never-again
  list, or was already written to for another company (each row says which;
  a row like that is refused on its own too, D3);
* ethics approval is not recorded (`SENDING_APPROVED` in `.env`);
* the draft's survey link is a placeholder, or points somewhere other than
  the profile's current `survey_url`.
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from company_reach.models import CompanyProfile, Contact, Draft, dotted_uid
from company_reach.nodes.check_draft import problems
from company_reach.profile import Profile
from company_reach.tools.db import (
    address_block,
    company_by_uid,
    contact_for,
    decision_for,
    is_suppressed,
    search_log,
    undoable_bounce,
)
from company_reach.tools.invitation import (
    FRAME_VERSION,
    is_placeholder_url,
    needs_check,
    salutation,
    split_name,
)
from company_reach.tools.mailto import build

_LEGAL_FORMS = {"0106": "AG", "0107": "GmbH"}
_TIERS = {
    "uid": "UID matches the register",
    "address": "address matches, no UID on the site",
    "model": "model choice",
}
_ORDER = {"send": 0, "hold": 1, "skip": 2}
# Where Frau / Herr came from, as the card says it next to the toggle.
_ORIGINS = {
    "reviewer": "your choice",
    "page": "written on the page",
    "page-surname": "on the page, surname only",
    "role": "from the role",
    "shab": "SHAB proposal",
    None: "none known",
}
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class DraftView:
    id: int
    subject: str
    body: str
    mailto_fits: bool
    prompt_version: str
    model: str
    model_text: str | None
    frame_version: str | None  # None: written before frame@1
    arm: str | None
    problems: str | None  # None: never checked; "": passed
    contact_id: int | None = None  # the contact row it was written for

    @property
    def link(self) -> str | None:
        found = _URL.findall(self.body)
        return found[0] if found else None

    def as_draft(self) -> Draft:
        return Draft(
            subject=self.subject,
            body=self.body,
            model_text=self.model_text or "",
            link=self.link or "",
            mailto_fits=self.mailto_fits,
            frame_version=self.frame_version or "",
            arm=self.arm or "voll",
        )

    @property
    def body_sha256(self) -> str:
        """The card's form carries it and Send compares it: the salutation
        toggle rebuilds a draft under the same id, so the id alone cannot
        tell the text the reviewer read from the one in the table now."""
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


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
    # the greeting's Frau / Herr, or None when it uses the full name
    salutation: str | None = None
    # where it came from, in the reviewer's words: "from the role", ...
    salutation_origin: str | None = None
    # "Anrede prüfen": neither the reviewer's choice nor a full page match
    check_salutation: bool = False
    # a named person with a surname, and a current draft to rebuild
    can_choose_salutation: bool = False
    # an undecided current draft: another address rebuilds it for that row
    can_readdress: bool = False
    # offered addresses Send refuses, with why: on the never-again list, or
    # already written to for another company
    address_blocks: dict[str, str] = field(default_factory=dict)
    # the row selected when the card opens: the first one Send accepts
    default_to: str | None = None
    # its bounce is the ledger's newest row: a mistaken click is undone
    can_undo_bounce: bool = False


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


def _drift(
    draft: DraftView | None,
    contact: Contact | None,
    contact_id: int | None,
    profile: Profile | None,
) -> str | None:
    """Why a checked frame@1 draft no longer matches what it was built
    from, or None. Pure code: the frame is rebuilt and compared, as
    `check_draft` does, against today's profile and contact."""
    if draft is None or draft.frame_version != FRAME_VERSION or draft.problems != "":
        return None  # _send_block says what is wrong with it
    if draft.contact_id is not None and draft.contact_id != contact_id:
        return "a newer contact was found since the draft was written"
    if profile is None or contact is None:
        return None
    found = problems(draft.as_draft(), contact, profile)
    if found:
        return f"the draft no longer matches the profile ({'; '.join(found)})"
    return None


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
        model_text=row["model_text"],
        frame_version=row["frame_version"],
        arm=row["arm"],
        problems=row["problems"],
        contact_id=row["contact_id"],
    )


def _address_blocks(
    conn: sqlite3.Connection, uid: str, contact: Contact | None
) -> dict[str, str]:
    if contact is None:
        return {}
    blocks = {}
    for row in contact.addresses:
        if row.kind == "third_party":
            continue  # never sent to anyway, and says so on its row
        if why := address_block(conn, row.email, uid=uid):
            blocks[row.email] = why
    return blocks


def _default_to(contact: Contact | None, blocks: dict[str, str]) -> str | None:
    for row in contact.addresses if contact else []:
        if row.kind != "third_party" and row.email not in blocks:
            return row.email
    return None


def _send_block(
    *,
    run_id: str,
    draft: DraftView | None,
    reason: str,
    decision: str | None,
    suppressed: bool,
    survey_url: str,
    sending_approved: bool,
    drift: str | None = None,
    no_address: str | None = None,
) -> str | None:
    if decision is not None:
        return f"Already decided: {decision}."
    if suppressed:
        return "This company is on the never-again list."
    if draft is None:
        return f"No draft to send — {reason}."
    if draft.frame_version != FRAME_VERSION:
        return (
            f"The draft was written with an older frame "
            f"({draft.frame_version or 'before frame@1'}); "
            f"run `company-reach redraft {run_id}`."
        )
    if draft.problems is None:
        return f"The draft was never checked; run `company-reach redraft {run_id}`."
    if draft.problems:
        return f"The draft failed its checks ({draft.problems}); redraft it."
    if no_address:
        return no_address
    if drift:
        return f"{drift[:1].upper()}{drift[1:]}; run `company-reach redraft {run_id}`."
    if not sending_approved:
        return (
            "Sending is locked until ethics approval is recorded "
            "(SENDING_APPROVED=true in .env)."
        )
    link = draft.link or ""
    if is_placeholder_url(link):
        host = urlsplit(link).hostname
        return (
            f"The survey link is a placeholder ({host}); set survey_url, then "
            f"run `company-reach redraft {run_id}`."
        )
    if not survey_url or not link.startswith(survey_url.rstrip("/")):
        return (
            "The draft links to a survey other than the profile's survey_url; "
            f"run `company-reach redraft {run_id}`."
        )
    return None


def load_cards(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    survey_url: str,
    sending_approved: bool,
    profile: Profile | None = None,
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
        company = (
            CompanyProfile.model_validate_json(profile_row["profile"])
            if profile_row
            else None
        )
        found = contact_for(conn, run_id, uid)
        contact_id, contact = found if found else (None, None)
        draft = _draft(conn, run_id, uid)
        latest = decision_for(conn, uid)
        decision = latest["status"] if latest else None
        reason = r["reason"] or (
            f"{r['error_kind']} error: {r['error_text']}" if r["error_kind"] else ""
        )
        to = contact.email if contact and contact.email else ""
        # the toggle rebuilds a current draft around its own sentence, so it
        # needs one, and a surname for "Frau Muster" to be written at all
        rebuildable = (
            draft is not None
            and draft.frame_version == FRAME_VERSION
            and bool(draft.model_text)
        )
        surname = bool(contact and contact.name and split_name(contact.name).surname)
        blocks = _address_blocks(conn, uid, contact)
        # frame@1 writes the mail for one address (contact.email); only that
        # row is sent from, and only while nothing refuses it (D3). Another
        # acceptable row is offered as a rebuild, never as a silent switch.
        written = contact.email if contact and contact.email else None
        default_to = written if written and written not in blocks else None
        if blocks and _default_to(contact, blocks) is None:
            no_address = (
                "No address on this card may be written to: "
                + "; ".join(f"{a} is {why}" for a, why in blocks.items())
                + "."
            )
        elif written and written in blocks:
            no_address = (
                f"{written} is {blocks[written]}; choose another address below "
                "and read the rebuilt mail."
            )
        else:
            no_address = None
        cards.append(
            Card(
                uid=uid,
                uid_dotted=dotted_uid(uid),
                name=record.name if record else uid,
                legal_form=_LEGAL_FORMS.get(record.legal_form, "") if record else "",
                seat=(record.city or record.municipality) if record else "",
                what=company.description
                if company
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
                    run_id=run_id,
                    drift=_drift(draft, contact, contact_id, profile),
                    draft=draft,
                    reason=reason,
                    decision=decision,
                    suppressed=is_suppressed(conn, uid),
                    survey_url=survey_url,
                    sending_approved=sending_approved,
                    no_address=no_address,
                ),
                salutation=salutation(contact)[0] if contact else None,
                salutation_origin=_ORIGINS[salutation(contact)[1]] if contact else None,
                check_salutation=bool(contact and draft and needs_check(contact)),
                can_choose_salutation=rebuildable and surname and decision is None,
                can_readdress=rebuildable and decision is None,
                address_blocks=blocks,
                default_to=default_to,
                can_undo_bounce=undoable_bounce(conn, uid) is not None,
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
