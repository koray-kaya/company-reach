"""The last deterministic line between page text and a mail someone sends.

The drafting prompt sees the company's profile, and the profile's
description can quote a hostile page by design (#22). So this is the guard
that matters rather than a tidiness rule:

* the model's own text carries no link and no e-mail address;
* the finished mail's only URL is the survey link code appended, byte for
  byte;
* it greets the contact by name, carries the data-protection sentence, is
  at most 1,200 characters and fits in a `mailto:` link.

The last three are assembled by code and should never fail; they are
checked anyway, because a check that only covers what can go wrong today
stops covering it the day the assembly changes.

A draft that fails is written once more, with the model told why. If the
second fails too, the company is held and the draft deleted, so nothing that
failed these checks is one click from being sent. What survives every rule
is non-URL steering — a sentence a hostile page talked the model into — and
for that the control is the human reading the draft before sending it.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from company_reach.models import Contact, Draft
from company_reach.nodes import draft as draft_node
from company_reach.settings import Settings
from company_reach.tools.db import connect, delete_draft
from company_reach.tools.invitation import privacy_sentence

MAX_CHARS = 1200

# A scheme, "www.", or a bare domain under a common TLD ("shop.ch/angebot").
# Ordinary German abbreviations ("z.B.", "St.Gallen", "30.9.2026") end in
# something that is not one of these.
_LINK = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:ch|li|com|net|org|de|at|fr|it|eu|io"
    r"|info|swiss|shop|example)\b\S*",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_IN_BODY = re.compile(r"https?://\S+")

Redraft = Callable[..., Awaitable[dict]]


def problems(draft: Draft, contact: Contact) -> list[str]:
    """Every rule the draft breaks, worded for the model's second attempt
    and for the reviewer's card. Empty means it may be sent."""
    found: list[str] = []
    text, body = draft.model_text, draft.body

    if email := _EMAIL.search(text):
        found.append(f"the text contained an e-mail address ({email.group()})")
        text = _EMAIL.sub(" ", text)  # so the address is not counted twice
    if link := _LINK.search(text):
        found.append(
            f"the text contained a link ({link.group()}); the survey link is "
            "added below the text by code"
        )
    if _URL_IN_BODY.findall(body) != [draft.link]:
        found.append("the survey link is not the only URL in the mail")
    if not body.startswith(draft_node.greeting(contact) + "\n"):
        found.append(f"the greeting does not name {contact.name or 'nobody'}")
    if privacy_sentence(contact.source) not in body:
        found.append("the data-protection sentence is missing")
    if len(body) > MAX_CHARS:
        found.append(f"the mail is longer than 1,200 characters ({len(body)})")
    if not draft.mailto_fits:
        found.append("the mail is too long for a mailto: link")
    return found


async def check_draft(
    state: dict[str, Any],
    *,
    settings: Settings,
    redraft: Redraft = draft_node.draft,
) -> dict:
    """`redraft` is the `draft` node, passed in so a test can hand it a
    double and count how often it was asked."""
    contact: Contact = state["contact"]
    found = problems(state["draft"], contact)
    if not found:
        return {}

    second = (
        await redraft(state | {"draft_feedback": "; ".join(found)}, settings=settings)
    )["draft"]
    found = problems(second, contact)
    if not found:
        return {"draft": second}

    with connect(settings.db_path) as conn:
        delete_draft(conn, state["run_id"], state["uid"])
    return {
        "draft": None,
        "recommendation": "hold",
        "reason": f"the draft failed its checks twice: {'; '.join(found)}",
    }
