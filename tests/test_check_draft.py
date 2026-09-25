"""`check_draft`: the last deterministic line between page text and a mail.

The drafting prompt sees the company's profile, and the profile's
description can quote a hostile page by design (#22). So this check is the
guard that matters, not a tidiness rule: the model's own sentence may carry
no link and no address, the only URL in the finished mail is the survey
link code appended, byte for byte, and the rest of the mail is exactly the
frame code writes around the sentence.
"""

import sqlite3

import pytest
from fictional_profile import INVITATION, SENDER

from company_reach.models import Contact, Draft
from company_reach.nodes.check_draft import check_draft, problems
from company_reach.profile import Profile
from company_reach.tools.db import connect, init_db, record_draft
from company_reach.tools.invitation import FRAME_VERSION, assemble, subject
from company_reach.tools.llm import Provenance

LINK = "https://survey.example/form/?c=CHE000000046&l=de"
GOOD = "Ich schreibe Ihnen, weil Ihr Betrieb Treppengeländer aus Stahl fertigt."
EVIL = GOOD + " Mehr Informationen: https://evil.example/offer"
PROFILE = Profile(
    goal="g",
    survey_url="https://survey.example/form",
    sender=SENDER,
    invitation=INVITATION,
)
PROV = Provenance(
    model="test-model",
    prompt="draft",
    prompt_version="4",
    reasoning_effort="low",
    prompt_tokens=1,
    completion_tokens=1,
    finish_reason="stop",
    seconds=0.1,
)


def contact(name: str | None = "Anna Muster") -> Contact:
    return Contact(
        name=name,
        role="Inhaberin",
        email="info@muster.ch",
        email_kind="generic",
        source="site",
    )


def make(text: str, c: Contact | None = None, **over) -> Draft:
    c = c or contact()
    body = assemble(c, text, link=LINK, sender=SENDER, inv=INVITATION)
    fields = {
        "subject": subject(c, SENDER, INVITATION),
        "body": body,
        "model_text": text,
        "link": LINK,
        "mailto_fits": True,
        "frame_version": FRAME_VERSION,
    } | over
    return Draft(**fields)


def found(d: Draft, c: Contact | None = None) -> list[str]:
    return problems(d, c or contact(), PROFILE)


# --- the rules ---------------------------------------------------------------


def test_a_clean_draft_passes():
    assert found(make(GOOD)) == []


@pytest.mark.parametrize(
    "planted",
    [
        "https://evil.example/offer",
        "http://evil.example",
        "www.evil.example",
        "evil-shop.ch/angebot",
    ],
)
def test_a_link_in_the_models_text_fails(planted: str):
    assert any("link" in p for p in found(make(f"{GOOD} Siehe {planted}.")))


def test_an_address_in_the_models_text_fails():
    text = f"{GOOD} Schreiben Sie an ceo@evil.example."
    assert any("e-mail" in p for p in found(make(text)))


def test_the_survey_link_must_be_the_only_url_in_the_body():
    d = make(GOOD)
    tampered = d.model_copy(update={"body": d.body.replace(LINK, LINK + "x")})
    assert any("survey link" in p for p in found(tampered))


def test_a_long_body_fails():
    assert any("longer than" in p for p in found(make(GOOD + " Mehr." * 300)))


def test_the_greeting_is_the_frames():
    d = make(GOOD)
    unnamed = d.model_copy(
        update={"body": d.body.replace("Guten Tag Frau Muster", "Hallo")}
    )
    assert any("frame" in p for p in found(unnamed))


def test_the_privacy_text_is_the_frames():
    d = make(GOOD)
    cut = d.model_copy(
        update={"body": d.body.replace("dann lösche ich Ihren Namen", "")}
    )
    assert any("frame" in p for p in found(cut))


def test_a_mail_too_long_for_mailto_fails():
    assert any("mailto" in p for p in found(make(GOOD, mailto_fits=False)))


def test_ordinary_german_is_not_mistaken_for_a_link():
    text = GOOD + " Z.B. in St.Gallen, ca. 15 Min., Nr. 3 u.a. bis 30.9.2026."
    assert not any("link" in p for p in found(make(text)))


# --- the node ----------------------------------------------------------------


class Redraft:
    """Stands in for the `draft` node: hands out the next prepared text and
    remembers the feedback it was given."""

    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.feedback: list[str] = []

    async def __call__(self, state, *, settings):
        self.feedback.append(state.get("draft_feedback", ""))
        d = make(self.texts.pop(0), state["contact"])
        store(settings, d)  # as the real node does
        return {"draft": d}


def store(settings, d: Draft) -> None:
    with connect(settings.db_path) as conn:
        record_draft(conn, "run-1", "CHE000000046", d, contact_id=1, provenance=PROV)


@pytest.fixture
def db_settings(settings):
    init_db(settings.db_path)
    return settings


def state(d: Draft) -> dict:
    return {
        "run_id": "run-1",
        "uid": "CHE000000046",
        "contact": contact(),
        "draft": d,
        "recommendation": "send",
        "reason": "Anna Muster at the general inbox the site publishes",
    }


def stored_drafts(settings) -> int:
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute("select count(*) from drafts").fetchone()[0]
    finally:
        conn.close()


async def test_a_passing_draft_is_left_alone(db_settings):
    redraft = Redraft()
    out = await check_draft(state(make(GOOD)), settings=db_settings, redraft=redraft)
    assert redraft.feedback == []
    assert out.get("recommendation", "send") == "send"


async def test_one_failure_is_redrafted_with_the_reason(db_settings):
    redraft = Redraft(GOOD)
    out = await check_draft(state(make(EVIL)), settings=db_settings, redraft=redraft)
    assert len(redraft.feedback) == 1
    assert "link" in redraft.feedback[0]
    assert out["draft"].model_text == GOOD
    assert out.get("recommendation", "send") == "send"


async def test_two_failures_hold_and_leave_nothing_to_send(db_settings):
    # the P1 case: a profile quoting an injected link, and a model that
    # repeats it both times
    redraft = Redraft(EVIL)
    first = make(EVIL)
    store(db_settings, first)
    out = await check_draft(state(first), settings=db_settings, redraft=redraft)
    assert out["recommendation"] == "hold"
    assert "link" in out["reason"]
    assert out["draft"] is None
    assert stored_drafts(db_settings) == 0
