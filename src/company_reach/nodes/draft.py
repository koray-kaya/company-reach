"""The invitation: one sentence from the model, everything else from code.

The model writes one sentence, "Ich schreibe Ihnen, weil ...", saying why
this company gets the mail. It is shown the company's profile (M6 open point
1) — a sentence proving we know what the company makes is what separates
this mail from a mass mailing — but the profile is delimited as data, since
its description can quote hostile page text by design (#22), and so is the
contact's role, which a model read off a page (audit K4). It is never shown
the page text itself, and never the survey link.

Code writes the rest (frame@1, `tools/invitation.py`): the subject, a
routing line for a shared inbox, the greeting, who writes and what they ask,
the topic, the link block, where the name and address came from, and the
signature. So whatever steered the model cannot change who is greeted,
where the link points, or what the reader is told about their data. The
sender comes from the profile's `[sender]`, not from the mail client: the
mail must say who writes even when the client adds no signature.
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
from company_reach.tools.invitation import (
    FRAME_VERSION,
    arm_for,
    assemble,
    subject,
    survey_link,
)
from company_reach.tools.mailto import build
from company_reach.tools.untrusted import as_data


async def draft(state: dict[str, Any], *, settings: Settings) -> dict:
    record: CompanyRecord = state["company"]
    profile: CompanyProfile = state["profile"]
    contact: Contact = state["contact"]

    me = load_profile(settings.profile_path)
    if gaps := me.drafting_gaps():
        # before the model is asked: a mail that cannot say who writes, or
        # where the survey is, should cost no model call
        raise ProfileError(
            f"{settings.profile_path} is missing what every invitation needs: "
            f"{', '.join(gaps)}"
        )
    # Built before the model is asked, too: a UID the survey would reject
    # should cost nothing.
    link = survey_link(me.survey_url, record.uid)
    arm = arm_for(record.uid, experiment=me.invitation.experiment)

    feedback = state.get("draft_feedback") or ""
    if feedback:
        # the second and last attempt, after `check_draft` rejected the first
        feedback = (
            "## An earlier sentence was rejected\n\n"
            f"{feedback}\n\nWrite a new one that does not do this."
        )

    answer, provenance = await llm.ask(
        "draft",
        DraftAnswer,
        settings=settings,
        about_me=state.get("about_me") or "",
        company_name=record.name,
        seat=record.city or record.municipality,
        role=as_data(contact.role, label="ROLE") if contact.role else "not known",
        profile=as_data(profile.description, label="PROFILE"),
        feedback=feedback,
    )

    sentence = answer.sentence.strip().replace("ß", "ss")
    body = assemble(
        contact,
        sentence,
        link=link,
        sender=me.sender,
        inv=me.invitation,
        short=arm == "kurz",
    )
    title = subject(contact, me.sender, me.invitation)
    result = Draft(
        subject=title,
        body=body,
        model_text=sentence,
        link=link,
        mailto_fits=build(contact.email or "", title, body).fits,
        frame_version=FRAME_VERSION,
        arm=arm,
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
