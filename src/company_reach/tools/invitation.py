"""The parts of an invitation the model never writes.

The survey link and the data-protection sentence are added by code after
the model has drafted the body. The model never sees the link, so page text
cannot talk it into planting a different one, and `check_draft` can require
that the only URL in a sent mail is this one, byte for byte.

The link's shape is fixed by the survey (decided 2026-09-24):
`<survey_url>/?c=<UID>&l=<de|en>`. The survey opens its follow-up questions
only for a UID whose check digit is valid, and reserves `SMOKE` for its own
smoke test, so an invalid UID must fail here rather than produce a link that
looks right and records nothing.
"""

import re
from typing import Literal

from company_reach.errors import CompanyReachError

_DIGITS = re.compile(r"\D")
# The UID register's weights for the first eight digits (mod 11).
_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)
_LANGS = ("de", "en")

# Approved by Koray on 2026-09-24. revDSG: a person whose data was not
# collected from them is told where it came from, what it is for, and how to
# have it deleted. "nicht wieder schreiben" is kept by the suppression list.
_PRIVACY = (
    "Ihren Namen und Ihre Adresse habe ich {source} entnommen. Ich verwende "
    "sie nur für diese Einladung; wenn Sie kurz antworten, lösche ich sie und "
    "schreibe Ihnen nicht wieder."
)
_SOURCES = {
    "site": "von Ihrer Website",
    "shab": "aus dem Schweizerischen Handelsamtsblatt (SHAB)",
}


class InvitationError(CompanyReachError):
    """A link or sentence could not be built from what was given."""


def _compact(uid: str) -> str | None:
    """`CHE-000.000.046`, `CHE000000046`, `CHE 000 000 046` → `CHE000000046`."""
    text = uid.strip().upper()
    if not text.startswith("CHE"):
        return None
    digits = _DIGITS.sub("", text[3:])
    return f"CHE{digits}" if len(digits) == 9 else None


def uid_is_valid(uid: str) -> bool:
    compact = _compact(uid)
    if compact is None:
        return False
    digits = [int(c) for c in compact[3:]]
    check = 11 - sum(w * d for w, d in zip(_WEIGHTS, digits[:8], strict=True)) % 11
    check = 0 if check == 11 else check
    return check != 10 and check == digits[8]


def survey_link(survey_url: str, uid: str, *, lang: str = "de") -> str:
    if not survey_url:
        raise InvitationError(
            "no survey_url in the profile; every invitation links to it"
        )
    if lang not in _LANGS:
        raise InvitationError(f"lang must be one of {_LANGS}, not {lang!r}")
    if not uid_is_valid(uid):
        raise InvitationError(f"{uid!r} is not a UID with a valid check digit")
    return f"{survey_url.rstrip('/')}/?c={_compact(uid)}&l={lang}"


def privacy_sentence(source: Literal["site", "shab"]) -> str:
    return _PRIVACY.format(source=_SOURCES[source])
