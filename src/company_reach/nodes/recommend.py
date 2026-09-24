"""Send, hold or skip — pure rules, no I/O, and always one reason.

Skip is about the company: it is not one the goal is after, whatever its
contact. Hold is about the address: the company may be right, but nothing
here should be written to without a human looking first. Send means a
reviewer can act on the card as it is; it never means the tool sends.

The reason is shown on the review card, so it says what the verdict rests
on in the reviewer's terms rather than naming a rule.
"""

from typing import Any

from company_reach.models import CompanyProfile, Contact

_SEND_REASONS = {
    "seen": "{who} at their own address on the company's site",
    "generic": "{who} at the general inbox the site publishes",
    "constructed": "{who} at info@ on the site's domain — not seen, a guess",
}


def recommend(state: dict[str, Any]) -> dict:
    profile: CompanyProfile | None = state.get("profile")
    contact: Contact | None = state.get("contact")

    if state.get("site") is None:
        return _verdict("skip", "no website of its own was found")
    if profile is not None and profile.distributor_only:
        return _verdict("skip", "its site says it resells what others make")
    if profile is not None and profile.foreign_group:
        return _verdict("skip", "its site belongs to a foreign parent group")

    if contact is None:
        return _verdict("hold", "nobody is named and no address is published")
    if contact.email is None and contact.name is None:
        return _verdict(
            "hold", "no address and nobody named; only a LinkedIn lead to check"
        )
    if contact.email is None:
        return _verdict(
            "hold", f"{contact.name} is named ({contact.source}) but no address"
        )
    if contact.email_kind == "third_party":
        return _verdict(
            "hold", f"the only address, {contact.email}, is on another domain"
        )

    who = contact.name or "nobody named"
    return _verdict("send", _SEND_REASONS[contact.email_kind].format(who=who))


def _verdict(recommendation: str, reason: str) -> dict:
    return {"recommendation": recommendation, "reason": reason}
