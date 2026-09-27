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
from urllib.parse import parse_qs, urlsplit

from company_reach.tools.invitation import InvitationError

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
