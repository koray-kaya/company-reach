"""Drafting the invitation: what the model is shown, and what code adds.

The model writes the subject and a few sentences. The greeting, the survey
link, the data-protection sentence and the closing are added by code, so a
page that steered the model still cannot change who is greeted, where the
link points, or what the reader is told about their data.
"""

import sqlite3

import pytest

from company_reach.errors import CompanyReachError
from company_reach.models import CompanyProfile, CompanyRecord, Contact
from company_reach.nodes import draft as node
from company_reach.nodes.draft import draft
from company_reach.tools.db import init_db
from company_reach.tools.invitation import privacy_sentence, survey_link
from company_reach.tools.llm import Provenance
from company_reach.tools.mailto import build

UID = "CHE000000046"
SURVEY = "https://survey.example/form"
MODEL_TEXT = (
    "Ich schreibe meine Masterarbeit darüber, wie kleine Firmen Kunden und "
    "Lieferanten finden. Als Hersteller von Metallteilen wäre Ihre Sicht "
    "dafür besonders wertvoll."
)
COMPANY = CompanyRecord(
    uid=UID,
    name="Muster Metallbau AG",
    legal_form="0106",
    municipality="3203",
    city="Musterstadt",
    purpose="Herstellung von Metallteilen.",
    purpose_head="Herstellung von Metallteilen.",
)
PROVENANCE = Provenance(
    model="test-model",
    prompt="draft",
    prompt_version="1",
    reasoning_effort="low",
    prompt_tokens=10,
    completion_tokens=20,
    finish_reason="stop",
    seconds=0.1,
)


def contact(name: str | None = "Anna Muster", source: str = "site") -> Contact:
    return Contact(
        name=name,
        role="Inhaberin",
        email="info@muster-metallbau.ch",
        email_kind="generic",
        source=source,
    )


def state(c: Contact | None = None) -> dict:
    return {
        "run_id": "run-1",
        "uid": UID,
        "company": COMPANY,
        "profile": CompanyProfile(description="Makes steel stair railings."),
        "contact": c or contact(),
        "contact_id": 7,
        "about_me": "A master's student writing a thesis.",
        "page_texts": {"https://muster-metallbau.ch/": "RAW PAGE TEXT"},
    }


@pytest.fixture
def db_settings(settings):
    init_db(settings.db_path)
    settings.profile_path.write_text(
        f'goal = "Firms that make things."\nsurvey_url = "{SURVEY}"\n'
    )
    return settings


@pytest.fixture
def shown(monkeypatch) -> dict[str, str]:
    """Stubs the model; hands back the variables the prompt was rendered
    with, so a test can read what the model would have been shown."""
    seen: dict[str, str] = {}

    async def ask(prompt_name, output_model, *, settings, **variables):
        seen.update(variables, prompt=prompt_name)
        return output_model(subject="Kurze Umfrage", body=MODEL_TEXT), PROVENANCE

    monkeypatch.setattr(node.llm, "ask", ask)
    return seen


# --- what the model sees -----------------------------------------------------


async def test_the_profile_is_shown_as_data(db_settings, shown):
    await draft(state(), settings=db_settings)
    assert shown["prompt"] == "draft"
    assert "<<<PROFILE>>>" in shown["profile"]
    assert "Makes steel stair railings." in shown["profile"]


async def test_no_page_text_reaches_the_prompt(db_settings, shown):
    await draft(state(), settings=db_settings)
    assert not any("RAW PAGE TEXT" in v for v in shown.values())


async def test_the_model_never_sees_the_survey_link(db_settings, shown):
    await draft(state(), settings=db_settings)
    assert not any("survey.example" in v for v in shown.values())


# --- what code adds ----------------------------------------------------------


async def test_the_body_is_assembled_around_the_model_text(db_settings, shown):
    out = await draft(state(), settings=db_settings)
    body = out["draft"].body
    assert body.startswith("Guten Tag Anna Muster\n\n")
    assert MODEL_TEXT in body
    assert survey_link(SURVEY, UID) in body
    assert privacy_sentence("site") in body
    assert body.rstrip().endswith("Freundliche Grüsse")
    assert out["draft"].model_text == MODEL_TEXT
    assert out["draft"].subject == "Kurze Umfrage"


async def test_a_shab_contact_is_told_the_name_came_from_shab(db_settings, shown):
    out = await draft(state(contact(source="shab")), settings=db_settings)
    assert privacy_sentence("shab") in out["draft"].body


async def test_an_inbox_with_nobody_named_is_greeted_without_a_name(db_settings, shown):
    # never "Sehr geehrte Damen und Herren": that is the mail that gets
    # forwarded rather than answered
    out = await draft(state(contact(name=None)), settings=db_settings)
    assert out["draft"].body.startswith("Guten Tag\n\n")


async def test_the_mailto_length_is_measured_on_the_final_body(db_settings, shown):
    out = await draft(state(), settings=db_settings)
    d = out["draft"]
    # the body carries the link, so the link is part of what is measured
    assert d.mailto_fits == build("info@muster-metallbau.ch", d.subject, d.body).fits


async def test_the_draft_is_stored_with_its_provenance(db_settings, shown):
    out = await draft(state(), settings=db_settings)
    conn = sqlite3.connect(db_settings.db_path)
    conn.row_factory = sqlite3.Row
    [row] = conn.execute("select * from drafts").fetchall()
    conn.close()
    assert (row["run_id"], row["uid"], row["contact_id"]) == ("run-1", UID, 7)
    assert row["body"] == out["draft"].body
    assert (row["prompt_version"], row["model"], row["mailto_fits"]) == (
        "1",
        "test-model",
        1,
    )


# --- what stops a draft ------------------------------------------------------


async def test_without_a_survey_url_there_is_no_draft(db_settings, shown):
    db_settings.profile_path.write_text('goal = "Firms that make things."\n')
    with pytest.raises(CompanyReachError, match="survey_url"):
        await draft(state(), settings=db_settings)


# --- a second attempt --------------------------------------------------------


async def test_a_redraft_is_told_why_the_first_was_rejected(db_settings, shown):
    rejected = state() | {"draft_feedback": "the text contained a link"}
    await draft(rejected, settings=db_settings)
    assert "the text contained a link" in shown["feedback"]


async def test_a_first_draft_carries_no_feedback(db_settings, shown):
    await draft(state(), settings=db_settings)
    assert shown["feedback"] == ""


async def test_the_link_travels_with_the_draft(db_settings, shown):
    # check_draft compares the body's only URL against it, byte for byte
    out = await draft(state(), settings=db_settings)
    assert out["draft"].link == survey_link(SURVEY, UID)
