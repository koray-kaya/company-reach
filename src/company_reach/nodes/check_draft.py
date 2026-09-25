"""The last deterministic line between page text and a mail someone sends.

The drafting prompt sees the company's profile, and the profile's
description can quote a hostile page by design (#22). So this is the guard
that matters rather than a tidiness rule. Under frame@1 the model writes one
sentence and code writes the rest, so the checks come in two kinds.

The sentence (`sentence_problems`), each rule worded as feedback for the one
redraft: it begins "Ich schreibe Ihnen, weil", is one German sentence of at
most 25 words, ends with a full stop, and carries no role noun (the wrong
gender is the worst mistake this mail can make), no praise or denial of
selling, nothing the frame already says, no legal form, no year, no
placeholder, no e-mail address and no link — written out, defanged, or
under any ending.

The mail (`problems`): the body is exactly what the frame builds around
that sentence, routing line, greeting, privacy text and signature included;
the subject is the frame's; the survey link is the body's only URL, byte for
byte; nothing the profile supplied is a placeholder; Swiss spelling; at most
1,300 characters, and it fits in a `mailto:` link, which is the binding
limit. The frame is assembled by code and should never fail; it is checked
anyway, because a check that only covers what can go wrong today stops
covering it the day the assembly changes.

A draft that fails is written once more, with the model told why. If the
second fails too, the company is held and the draft deleted; if the second
attempt raises, the first is deleted before the error travels on. The
outcome is stored on the draft row, and the card sends only a draft that
passed. What survives every rule is non-URL steering — a sentence a hostile
page talked the model into — and for that the control is the human reading
the draft before sending it.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from company_reach.models import Contact, Draft
from company_reach.nodes import draft as draft_node
from company_reach.profile import Profile, load_profile
from company_reach.settings import Settings
from company_reach.tools.db import connect, delete_draft, record_draft_check
from company_reach.tools.invitation import (
    FRAME_VERSION,
    arm_for,
    assemble,
    subject,
    survey_link,
)
from company_reach.tools.mailto import build

PREFIX = "Ich schreibe Ihnen, weil "
MAX_WORDS = 25  # the prompt asks for 20
MAX_SENTENCE_CHARS = 180
# The frame alone is about 1,000 characters with long names; the study's
# longest fictional mail measured 1,276.
MAX_CHARS = 1300

# A scheme, "www.", or a bare domain under a common TLD ("shop.ch/angebot").
# Ordinary German abbreviations ("z.B.", "St.Gallen", "30.9.2026") end in
# something that is not one of these.
_LINK = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:ch|li|com|net|org|de|at|fr|it|eu|io"
    r"|info|swiss|shop|example)\b\S*",
    re.IGNORECASE,
)
# Any other ending, in lower case: "evil.xyz", "shop.app". Upper case after
# the dot is German ("St.Gallen", "z.B."), and one letter is an abbreviation.
_ANY_DOMAIN = re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[a-z]{2,24}\b")
# A link or address written so a filter does not see it.
_DEFANGED = re.compile(
    r"\bh[tx]{2}ps?\b|[\[({]\s*(?:\.|dot|punkt|at|ät|@)\s*[\])}]"
    r"|\s(?:dot|punkt)\s+(?:ch|li|com|net|org|de|at|io|info|swiss)\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_IN_BODY = re.compile(r"https?://\S+")

# A role noun describes the reader, and in the wrong gender it is the worst
# mistake this mail can make; code writes the greeting. Case-insensitive
# because a compound writes the noun in lower case ("Mitinhaberin").
_ROLE = re.compile(
    r"\b(Mit)?(Inhaber|Geschäftsführer|Gründer|Chef|Leiter|Präsident|Direktor"
    r"|Unternehmer|Teilhaber|Verwaltungsrat|Verwaltungsrätin|CEO)(in|innen)?\b",
    re.IGNORECASE,
)
# Praise, denials of selling, and what the frame already says.
_BANNED = re.compile(
    r"\b(wertvoll\w*|besonders|genau|spannend\w*|innovativ\w*|führend\w*"
    r"|renommiert\w*|einzigartig\w*|beeindruckend\w*|aus erster Hand"
    r"|aus eigener Erfahrung|Praktiker\w*|Anruf\w*|Telefon\w*|Termin\w*"
    r"|Treffen|Verkaufsabsicht|Umfrage\w*|Fragebogen\w*|Masterarbeit|Minuten"
    r"|Link|Danke|Dank|Grüsse)\b",
    re.IGNORECASE,
)
_LEGAL_FORM = re.compile(r"\b(AG|GmbH|SA|Sàrl|KG|Co\.)(?=\W|$)")
_YEAR = re.compile(r"\b(18|19|20)\d{2}\b")
_PLACEHOLDER = re.compile(r"[\[\]{}<>®™©]|\bXY\b|\bXX\b|\.\.\.|…")
# A second sentence: "!" or "?", or a full stop after a word of four letters
# or more and before a capital. "St. Gallen" and "z. B." pass.
_SECOND = re.compile(r"[!?]|\b\w{4,}\.\s+[A-ZÄÖÜ]")
# Words German does not use. Two of them make the sentence English.
_ENGLISH = re.compile(
    r"\b(the|and|of|with|your|our|which|that|is|are|for|from|this|we)\b",
    re.IGNORECASE,
)

Redraft = Callable[..., Awaitable[dict]]


def _links(text: str) -> str | None:
    for pattern in (_LINK, _ANY_DOMAIN, _DEFANGED):
        if found := pattern.search(text):
            return found.group().strip()
    return None


def sentence_problems(text: str) -> list[str]:
    """What is wrong with the model's one sentence, worded as feedback."""
    found: list[str] = []
    if "\n" in text:
        found.append("the sentence contains a line break")
    if not text.startswith(PREFIX):
        found.append(f"the sentence must begin with '{PREFIX.strip()}'")
    if not text.rstrip().endswith("."):
        found.append("the sentence must end with a full stop")
    if _SECOND.search(text):
        found.append("write one sentence, not two, and no question")
    words = len(text.split())
    if words > MAX_WORDS or len(text) > MAX_SENTENCE_CHARS:
        found.append(
            f"the sentence is longer than {MAX_WORDS} words or "
            f"{MAX_SENTENCE_CHARS} characters ({words} words); write at most 20"
        )
    if role := _ROLE.search(text):
        found.append(
            f"the sentence describes the reader with a role noun ({role.group()})"
        )
    if banned := _BANNED.search(text):
        found.append(
            f"the sentence uses '{banned.group()}', which the rest of the mail "
            "already covers or which reads as praise or selling"
        )
    if legal := _LEGAL_FORM.search(text):
        found.append(
            f"write the company's name without its legal form ({legal.group()})"
        )
    if year := _YEAR.search(text):
        found.append(f"leave out years ({year.group()})")
    if placeholder := _PLACEHOLDER.search(text):
        found.append(
            f"the sentence contains a placeholder or symbol ({placeholder.group()})"
        )
    if len({w.lower() for w in _ENGLISH.findall(text)}) >= 2:
        found.append("write the sentence in German")
    if email := _EMAIL.search(text):
        found.append(f"the sentence contains an e-mail address ({email.group()})")
        text = _EMAIL.sub(" ", text)  # so the address is not counted twice
    if link := _links(text):
        found.append(
            f"the sentence contains a link ({link}); the survey link is added "
            "below it by code"
        )
    return found


def problems(draft: Draft, contact: Contact, profile: Profile) -> list[str]:
    """Every rule the draft breaks, worded for the model's second attempt
    and for the reviewer's card. Empty means it may be sent."""
    found = sentence_problems(draft.model_text)
    body = draft.body

    if _URL_IN_BODY.findall(body) != [draft.link]:
        found.append("the survey link is not the only URL in the mail")
    framed = assemble(
        contact,
        draft.model_text,
        link=draft.link,
        sender=profile.sender,
        inv=profile.invitation,
        short=draft.arm == "kurz",
    )
    if body != framed:
        found.append(
            "the mail is not what the frame writes around the sentence "
            "(routing line, greeting, privacy text or signature changed)"
        )
    if draft.subject != subject(contact, profile.sender, profile.invitation):
        found.append("the subject is not the one the frame writes")
    if _EMAIL.search(draft.subject) or _links(draft.subject):
        found.append(f"the subject contains a link or address ({draft.subject})")

    # What the profile supplied: everything but the sentence and the link.
    frame = body.replace(draft.model_text, "").replace(draft.link, "")
    if placeholder := _PLACEHOLDER.search(f"{draft.subject}\n{frame}"):
        found.append(
            f"the mail contains a placeholder ({placeholder.group()}); fill in "
            "[sender] and [invitation] in profile.toml"
        )
    if "ß" in body or "ß" in draft.subject:
        found.append("Swiss spelling: write ss, never ß")
    if len(body) > MAX_CHARS:
        found.append(f"the mail is longer than 1,300 characters ({len(body)})")
    if not draft.mailto_fits:
        found.append("the mail is too long for a mailto: link")
    return found


def reassemble(
    sentence: str, contact: Contact, profile: Profile, uid: str
) -> tuple[Draft, list[str]]:
    """The mail rebuilt by code around a sentence the model already wrote,
    from today's profile and contact — no model call — and checked again.
    Used when only the frame is stale: a new survey_url or closing date, a
    newer contact, the reviewer's salutation or address."""
    link = survey_link(profile.survey_url, uid)
    arm = arm_for(uid, experiment=profile.invitation.experiment)
    body = assemble(
        contact,
        sentence,
        link=link,
        sender=profile.sender,
        inv=profile.invitation,
        short=arm == "kurz",
    )
    title = subject(contact, profile.sender, profile.invitation)
    rebuilt = Draft(
        subject=title,
        body=body,
        model_text=sentence,
        link=link,
        mailto_fits=build(contact.email or "", title, body).fits,
        frame_version=FRAME_VERSION,
        arm=arm,
    )
    return rebuilt, problems(rebuilt, contact, profile)


async def check_draft(
    state: dict[str, Any],
    *,
    settings: Settings,
    redraft: Redraft = draft_node.draft,
) -> dict:
    """`redraft` is the `draft` node, passed in so a test can hand it a
    double and count how often it was asked."""
    run_id, uid = state["run_id"], state["uid"]
    contact: Contact = state["contact"]
    profile = load_profile(settings.profile_path)
    found = problems(state["draft"], contact, profile)
    if not found:
        with connect(settings.db_path) as conn:
            record_draft_check(conn, run_id, uid, [])
        return {}

    try:
        second = (
            await redraft(
                state | {"draft_feedback": "; ".join(found)}, settings=settings
            )
        )["draft"]
    except Exception:
        # the first draft failed; nothing that failed may stay one click
        # from being sent while the error travels to the result row
        with connect(settings.db_path) as conn:
            delete_draft(conn, run_id, uid)
        raise
    found = problems(second, contact, profile)
    if not found:
        with connect(settings.db_path) as conn:
            record_draft_check(conn, run_id, uid, [])
        return {"draft": second}

    with connect(settings.db_path) as conn:
        delete_draft(conn, run_id, uid)
    return {
        "draft": None,
        "recommendation": "hold",
        "reason": f"the draft failed its checks twice: {'; '.join(found)}",
    }
