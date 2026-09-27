"""Personal links and the contact log (#68).

A personal link is the survey's address with a code for one person,
`<survey>/?c=P-7K3Q9X&l=de`, for someone reached outside the tool's own
mails: on LinkedIn, by phone, in person. The survey stores the code with
the answers and never a name (its design, "What is not stored"), so code
and person meet only here, in `invites`. The Contacts page makes the code,
records the person, and lists every contact, the tool's mails from the
ledger and these links, with what the survey says about each.

Codes use the survey's alphabet: no 0, O, 1, I or L, so a code read aloud
cannot be misread. They come from `secrets`, not `random`: a code someone
could guess would let them answer in another person's name.
"""

import re
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from company_reach.tools.db import TAKEN_BACK, is_suppressed, now
from company_reach.tools.invitation import (
    InvitationError,
    compact_uid,
    survey_link,
    uid_is_valid,
)

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE = re.compile(r"^P-[2-9A-HJKMNP-Z]{6}$")
# what the page offers; the key is stored, the label is shown
CHANNELS = {
    "linkedin": "LinkedIn",
    "email": "E-mail",
    "phone": "Phone",
    "meeting": "In person",
    "other": "Other",
}
TOOL_MAIL = "Mail from this tool"  # the channel of a ledger row
_LANGS = ("de", "en")


class InviteError(ValueError):
    """A link that must not be made, and why, in words for the page."""


def new_code(
    conn: sqlite3.Connection, *, choice: Callable[[str], str] = secrets.choice
) -> str:
    """A code no invite holds yet. 31 to the sixth is about 887 million, so
    a clash is rare, but a second person given the same code would answer
    as the first."""
    while True:
        code = "P-" + "".join(choice(ALPHABET) for _ in range(6))
        if not conn.execute("select 1 from invites where code = ?", (code,)).fetchone():
            return code


def personal_link(survey_url: str, code: str, *, lang: str = "de") -> str:
    """The survey link for one person: a company's link with the code
    where the UID goes (`invitation.survey_link`)."""
    if not survey_url:
        raise InvitationError("no survey_url in the profile; every link goes to it")
    if lang not in _LANGS:
        raise InvitationError(f"lang must be one of {_LANGS}, not {lang!r}")
    if not CODE.match(code):
        raise InvitationError(f"{code!r} is not a personal code (P-XXXXXX)")
    return f"{survey_url.rstrip('/')}/?c={code}&l={lang}"


def code_in(text: str) -> str | None:
    """The personal code a text names, typed or as the `c=` of a pasted
    link, in upper case; None when it names none."""
    value = text.strip()
    value = parse_qs(urlsplit(value).query).get("c", [value])[0].strip().upper()
    return value if CODE.match(value) else None


def profile_key(url: str | None) -> str | None:
    """A profile address as one key, however it was copied: no scheme, no
    `www.`, no country subdomain, no query, no trailing slash, lower case.
    `https://www.linkedin.com/in/Anna-Muster/?utm=x` gives
    `linkedin.com/in/anna-muster`."""
    if not url or not url.strip():
        return None
    text = url.strip()
    parts = urlsplit(text if "://" in text else f"https://{text}")
    host = parts.netloc.lower().removeprefix("www.")
    if host.endswith(".linkedin.com"):  # ch.linkedin.com: the same profile
        host = "linkedin.com"
    return f"{host}{parts.path.rstrip('/').lower()}" if host else None


# the longest each field may be; the page's inputs carry the same maxlength
_LIMITS = {"person": 120, "company": 200, "profile": 300, "note": 500}


@dataclass(frozen=True)
class NewInvite:
    """What the page's form sends, as typed."""

    person: str
    company: str
    uid: str = ""
    channel: str = "linkedin"
    profile: str = ""
    note: str = ""


@dataclass(frozen=True)
class Invite:
    code: str
    person: str | None  # None once forgotten or purged
    company: str
    uid: str | None
    channel: str  # a CHANNELS key
    profile: str | None  # a profile_key
    note: str | None
    created_at: str


@dataclass(frozen=True)
class LogRow:
    """One contact on the page: a mail the tool sent, or a personal link."""

    when: str
    person: str | None
    company: str
    channel: str  # a CHANNELS label, or TOOL_MAIL
    tag: str  # what the survey stores with the answers: a code or a UID
    link: str | None  # the German link, to copy again
    profile: str | None
    started: str | None
    completed: str | None
    removable: bool  # a personal link nobody answered yet

    @property
    def answer(self) -> str:
        if self.completed:
            return "completed"
        return "started" if self.started else "not yet"


def _same_name(a: str, b: str) -> bool:
    """Company names compared as a person types them: case and spacing
    aside. Python's casefold, not SQL's lower(), which leaves Ü alone."""
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _resolve_uid(conn: sqlite3.Connection, company: str, uid: str) -> str | None:
    """The register number: the one typed, if its check digit holds, else
    the one pooled company of exactly that name. Two of one name, or none,
    give None: a wrong number would tie the link to another company."""
    if uid.strip():
        if not uid_is_valid(uid):
            raise InviteError(
                f"{uid.strip()} is not a UID: its check digit does not match."
            )
        return compact_uid(uid)
    matches = [
        r["uid"]
        for r in conn.execute("select uid, name from companies")
        if _same_name(r["name"], company)
    ]
    return matches[0] if len(matches) == 1 else None


def _earlier_contacts(
    conn: sqlite3.Connection, company: str, uid: str | None
) -> list[str]:
    notes = []
    if uid:
        mailed = conn.execute(
            f"""select max(decided_at) from ledger
                 where uid = ? and status = 'sent' and id not in {TAKEN_BACK}""",
            (uid,),
        ).fetchone()[0]
        if mailed:
            notes.append(f"The tool mailed this company on {mailed[:10]}.")
    for r in conn.execute(
        "select person, uid, company, created_at from invites order by created_at"
    ):
        if (uid and r["uid"] == uid) or _same_name(r["company"], company):
            who = r["person"] or "Someone whose name was forgotten"
            notes.append(
                f"{who} at this company got a personal link on {r['created_at'][:10]}."
            )
    return notes


def check(conn: sqlite3.Connection, new: NewInvite) -> tuple[str | None, list[str]]:
    """The company's UID and what to know before the link is made. Raises
    InviteError when it must not be made: a missing name or company, a
    channel the page does not offer, the never-again list, a person who
    already has a link. A second contact at a company is asked, not
    refused: a second person there can be the right one."""
    for name, limit in _LIMITS.items():
        if len(getattr(new, name)) > limit:
            raise InviteError(f"The {name} is longer than {limit} characters.")
    if not new.person.strip():
        raise InviteError("Who is the link for? The person's name is missing.")
    if not new.company.strip():
        raise InviteError("Which company does the person work for?")
    if new.channel not in CHANNELS:
        raise InviteError(f"Unknown channel {new.channel!r}.")
    uid = _resolve_uid(conn, new.company, new.uid)
    profile = profile_key(new.profile)
    if uid and is_suppressed(conn, uid):
        raise InviteError("This company is on the never-again list. No link was made.")
    if profile and is_suppressed(conn, profile):
        raise InviteError("This person asked to be forgotten. No link was made.")
    if profile:
        row = conn.execute(
            "select code, created_at from invites where profile = ?", (profile,)
        ).fetchone()
        if row:
            raise InviteError(
                f"This person already has a link, made on {row['created_at'][:10]}"
                f" ({row['code']}). Copy it from the list below."
            )
    return uid, _earlier_contacts(conn, new.company, uid)


def record_invite(
    conn: sqlite3.Connection, new: NewInvite, *, uid: str | None
) -> Invite:
    """Record the link. Call `check` first: this writes what it is given."""
    invite = Invite(
        code=new_code(conn),
        person=" ".join(new.person.split()),
        company=" ".join(new.company.split()),
        uid=uid,
        channel=new.channel,
        profile=profile_key(new.profile),
        note=new.note.strip() or None,
        created_at=now(),
    )
    conn.execute(
        """insert into invites
               (code, person, company, uid, channel, profile, note, created_at)
           values (?,?,?,?,?,?,?,?)""",
        (
            invite.code,
            invite.person,
            invite.company,
            invite.uid,
            invite.channel,
            invite.profile,
            invite.note,
            invite.created_at,
        ),
    )
    return invite


def invite_for(conn: sqlite3.Connection, code: str) -> Invite | None:
    row = conn.execute("select * from invites where code = ?", (code,)).fetchone()
    return Invite(**dict(row)) if row else None


def remove_invite(conn: sqlite3.Connection, code: str) -> bool:
    """A link made by mistake, before anyone answered it: deleted, not
    kept, since it was never a contact. A link someone answered stays."""
    return (
        conn.execute(
            "delete from invites where code = ?"
            " and code not in (select uid from responses)",
            (code,),
        ).rowcount
        == 1
    )


def clear_people(conn: sqlite3.Connection, codes: list[str]) -> int:
    """For forget and purge: the person, the profile and the note go; the
    row stays, so the company still counts as contacted. Returns the rows
    that still held something."""
    if not codes:
        return 0
    marks = ",".join("?" * len(codes))
    return conn.execute(
        f"""update invites set person = null, profile = null, note = null
             where code in ({marks})
               and (person is not null or profile is not null or note is not null)""",
        codes,
    ).rowcount


def contact_log(conn: sqlite3.Connection, survey_url: str | None) -> list[LogRow]:
    """Everyone contacted, newest first: each personal link, and each
    company's latest mail that was not taken back, with the survey's
    answer to either. A mail names its person while the contact row lasts
    (forget and purge delete it)."""

    def link(tag: str) -> str | None:
        if not survey_url:
            return None
        try:
            if CODE.match(tag):
                return personal_link(survey_url, tag)
            return survey_link(survey_url, tag)
        except InvitationError:
            return None

    rows = [
        LogRow(
            when=r["created_at"],
            person=r["person"],
            company=r["company"],
            channel=CHANNELS.get(r["channel"], r["channel"]),
            tag=r["code"],
            link=link(r["code"]),
            profile=r["profile"],
            started=r["started_at"],
            completed=r["completed_at"],
            removable=r["started_at"] is None and r["completed_at"] is None,
        )
        for r in conn.execute(
            """select i.*, r.started_at, r.completed_at
                 from invites i left join responses r on r.uid = i.code"""
        )
    ]
    rows += [
        LogRow(
            when=r["decided_at"],
            person=r["person"],
            company=r["company"] or r["uid"],
            channel=TOOL_MAIL,
            tag=r["uid"],
            link=link(r["uid"]),
            profile=None,
            started=r["started_at"],
            completed=r["completed_at"],
            removable=False,
        )
        for r in conn.execute(
            f"""with mails as (
                    select * from ledger
                     where status = 'sent' and id not in {TAKEN_BACK})
                select l.decided_at, l.uid, c.name as company, k.name as person,
                       r.started_at, r.completed_at
                  from mails l
                  left join companies c on c.uid = l.uid
                  left join drafts d on d.id = l.draft_id
                  left join contacts k on k.id = d.contact_id
                  left join responses r on r.uid = l.uid
                 where l.id = (select max(id) from mails where uid = l.uid)"""
        )
    ]
    return sorted(rows, key=lambda r: r.when, reverse=True)
