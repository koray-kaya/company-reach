"""Swiss enterprise identification numbers as they appear on a web page.

A UID from the register needs no checking — it is the register. A nine-digit
number found in page text does: it might be an order number, a phone number,
or the VAT id of the group's parent. The check digit is what separates a real
UID from a number that merely looks like one, and it is why a tier 1 match is
allowed to outrank the model's reading of the whole page.
"""

import re
from typing import Literal

# CHE, then nine digits in any of the spellings seen in the field, optionally
# followed by the tax-register suffix. The separators are a character class
# rather than a literal because an en dash turns up in hand-typed footers.
_UID_RE = re.compile(
    r"CHE[\s\-–.]?(\d{3})[\s.]?(\d{3})[\s.]?(\d{3})"
    r"(?:\s*(?:MWST|TVA|IVA|VAT|HR))?",
    re.IGNORECASE,
)

# Positional weights from the federal specification.
_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)

UidMatch = Literal["match", "different", "absent"]


def normalise_uid(written: str) -> str | None:
    """`CHE-000.000.046 MWST` and `CHE 000 000 046` both become
    `CHE000000046`. None when the text holds no UID-shaped number."""
    found = _UID_RE.search(written)
    return f"CHE{found.group(1)}{found.group(2)}{found.group(3)}" if found else None


def is_valid_uid(uid: str) -> bool:
    """Weighted sum of the first eight digits, mod 11; the check digit is
    11 minus the remainder, where 11 counts as 0 and 10 makes the number
    invalid — no UID is ever issued with that combination."""
    digits = re.sub(r"\D", "", uid)
    if len(digits) != 9:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], _WEIGHTS, strict=True))
    expected = 11 - total % 11
    if expected == 10:
        return False
    return int(digits[8]) == (0 if expected == 11 else expected)


def find_uids(text: str) -> list[str]:
    """Every valid UID in the text, in order, without repeats. Candidates
    that fail the check digit are dropped silently — that is the point of
    having one."""
    seen: list[str] = []
    for found in _UID_RE.finditer(text):
        uid = f"CHE{found.group(1)}{found.group(2)}{found.group(3)}"
        if is_valid_uid(uid) and uid not in seen:
            seen.append(uid)
    return seen


def uid_match(register_uid: str, page_text: str) -> UidMatch:
    """How the page's UIDs relate to the one the register gave us.

    Three answers, not two. A *different* valid UID is not the absence of
    evidence — it says the page belongs to a related but different company,
    usually the group's parent, and that is worth recording rather than
    flattening into "no match"."""
    found = find_uids(page_text)
    if not found:
        return "absent"
    return "match" if register_uid in found else "different"
