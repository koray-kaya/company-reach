"""The invitation: a few sentences from the model, everything else from code.

The model writes the subject and the middle of the mail. It is shown the
company's profile (M6 open point 1) — a sentence proving we know what the
company makes is what separates this mail from a mass mailing — but the
profile is delimited as data, since its description can quote hostile page
text by design (#22). It is never shown the page text itself, and never the
survey link.

Code then puts the mail together around that text:

    Guten Tag <name>

    <the model's text>

    <survey link>

    <data-protection sentence>

    Freundliche Grüsse

so that whatever steered the model cannot change who is greeted, where the
link points, or what the reader is told about their data. The sender's
name comes from the mail client's signature, since the mail leaves from the
sender's own mailbox.
"""

from typing import Any

from company_reach.errors import ProfileError
from company_reach.models import (
    CompanyProfile,
    CompanyRecord,
    Contact,
    Draft,
    DraftAnswer,
)
from company_reach.profile import load_profile
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import connect, record_draft
from company_reach.tools.invitation import privacy_sentence, survey_link
from company_reach.tools.mailto import build
from company_reach.tools.untrusted import as_data

CLOSING = "Freundliche Grüsse"


def greeting(contact: Contact) -> str:
    """Gender-neutral, so no guess about Herr or Frau is ever needed. With
    nobody named it stays "Guten Tag" — never "Sehr geehrte Damen und
    Herren", the greeting the research found gets forwarded, not read."""
    return f"Guten Tag {contact.name}" if contact.name else "Guten Tag"


def assemble(contact: Contact, model_text: str, *, link: str) -> str:
    parts = [
        greeting(contact),
        model_text.strip(),
        link,
        privacy_sentence(contact.source),
        CLOSING,
    ]
    return "\n\n".join(parts)


async def draft(state: dict[str, Any], *, settings: Settings) -> dict:
    record: CompanyRecord = state["company"]
    profile: CompanyProfile = state["profile"]
    contact: Contact = state["contact"]

    survey_url = load_profile(settings.profile_path).survey_url
    if not survey_url:
        raise ProfileError(
            f"{settings.profile_path} has no survey_url; every invitation links to it"
        )
    # Built before the model is asked: a UID the survey would reject should
    # cost no model call.
    link = survey_link(survey_url, record.uid)

    answer, provenance = await llm.ask(
        "draft",
        DraftAnswer,
        settings=settings,
        about_me=state.get("about_me") or "",
        company_name=record.name,
        seat=record.city or record.municipality,
        role=contact.role or "not known",
        profile=as_data(profile.description, label="PROFILE"),
    )

    body = assemble(contact, answer.body, link=link)
    result = Draft(
        subject=answer.subject.strip(),
        body=body,
        model_text=answer.body.strip(),
        mailto_fits=build(contact.email or "", answer.subject.strip(), body).fits,
    )
    with connect(settings.db_path) as conn:
        record_draft(
            conn,
            state["run_id"],
            state["uid"],
            result,
            contact_id=state.get("contact_id"),
            provenance=provenance,
        )
    return {"draft": result}
