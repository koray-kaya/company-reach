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

from company_reach.errors import LlmError
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
    body = make(GOOD).body
    padded = make(GOOD, body=body + " " * (1301 - len(body)))
    assert any("longer than 1,300" in p for p in found(padded))


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


# --- the sentence (frame@1) --------------------------------------------------
#
# Each rule is worded as feedback for the one redraft, so the model is told
# what to change, not only that something failed.


def sentence(text: str) -> list[str]:
    return found(make(text))


def test_the_fallback_sentence_passes():
    # what draft@4 writes when the profile says too little
    assert (
        sentence(
            "Ich schreibe Ihnen, weil ich bei der Suche nach KMU in Musterstadt "
            "auf Ihre Firma gestossen bin."
        )
        == []
    )


def test_a_sentence_without_the_prefix_fails():
    assert any(
        "Ich schreibe Ihnen, weil" in p
        for p in sentence("Ihre Firma fertigt Treppengeländer aus Stahl.")
    )
    # a lower-case start is not the prefix either
    assert sentence("ich schreibe Ihnen, weil Ihre Firma Teile fertigt.")


def test_a_sentence_must_end_with_a_full_stop():
    assert any(
        "full stop" in p
        for p in sentence("Ich schreibe Ihnen, weil Ihre Firma Teile fertigt")
    )


@pytest.mark.parametrize(
    "noun", ["Inhaber", "Geschäftsführerin", "Mitinhaberin", "Gründer", "CEO"]
)
def test_a_role_noun_fails(noun):
    text = f"Ich schreibe Ihnen, weil Sie als {noun} Treppengeländer fertigen."
    assert any("role noun" in p for p in sentence(text))


@pytest.mark.parametrize(
    "text",
    [
        "Ich schreibe Ihnen, weil Ihre Sicht besonders wertvoll ist.",
        "Ich schreibe Ihnen, weil Ihre innovative Firma Teile fertigt.",
        "Ich schreibe Ihnen, weil ich ohne Verkaufsabsicht und ohne Anruf frage.",
        "Ich schreibe Ihnen, weil Ihre Firma für meine Umfrage passt.",
        "Ich schreibe Ihnen, weil Ihre Firma Teile fertigt, vielen Dank.",
    ],
)
def test_praise_and_denial_fail(text):
    assert any("reads as praise or selling" in p for p in sentence(text))


def test_a_year_or_legal_form_fails():
    assert any(
        "years" in p
        for p in sentence(
            "Ich schreibe Ihnen, weil Ihre Firma seit 1987 Teile fertigt."
        )
    )
    assert any(
        "legal form" in p
        for p in sentence(
            "Ich schreibe Ihnen, weil die Muster Metallbau AG Teile fertigt."
        )
    )


def test_two_sentences_fail_but_st_gallen_passes():
    assert any(
        "one sentence" in p
        for p in sentence(
            "Ich schreibe Ihnen, weil Ihre Firma Teile fertigt. Das passt."
        )
    )
    assert any(
        "one sentence" in p
        for p in sentence("Ich schreibe Ihnen, weil Ihre Firma Teile fertigt, oder?")
    )
    assert (
        sentence(
            "Ich schreibe Ihnen, weil Ihre Firma in St. Gallen z. B. Treppen fertigt."
        )
        == []
    )


def test_a_long_sentence_fails():
    long = (
        "Ich schreibe Ihnen, weil Ihre Firma " + "Teile und " * 12 + "Treppen fertigt."
    )
    assert any("longer than" in p for p in sentence(long))


@pytest.mark.parametrize(
    "text",
    [
        "Ich schreibe Ihnen, weil ich an der [Hochschule] forsche.",
        "Ich schreibe Ihnen, weil <Firma> Teile fertigt.",
        "Ich schreibe Ihnen, weil Ihre Firma XY fertigt.",
        "Ich schreibe Ihnen, weil Ihre Firma ... fertigt.",
        "Ich schreibe Ihnen, weil Ihre Firma Muster® fertigt.",
    ],
)
def test_a_placeholder_fails(text):
    assert any("placeholder" in p for p in sentence(text))


def test_a_placeholder_in_the_profile_is_a_problem():
    # the "[Hochschule]" case: a profile field never filled in reaches the
    # frame, not the sentence
    unfilled = PROFILE.model_copy(
        update={"sender": SENDER.model_copy(update={"affiliation": "[Hochschule]"})}
    )
    c = contact()
    body = assemble(c, GOOD, link=LINK, sender=unfilled.sender, inv=INVITATION)
    d = make(GOOD, body=body)
    assert any("profile.toml" in p for p in problems(d, c, unfilled))


def test_an_english_sentence_fails():
    text = "Ich schreibe Ihnen, weil your company makes the best parts for us."
    assert any("German" in p for p in sentence(text))


@pytest.mark.parametrize(
    "planted",
    ["evil.xyz/offer", "shop.app", "evil[.]com", "hxxps://evil", "evil (dot) com"],
)
def test_a_defanged_or_unlisted_link_fails(planted):
    text = f"Ich schreibe Ihnen, weil Ihre Firma bei {planted} Teile verkauft."
    assert any("link" in p for p in sentence(text))


def test_an_eszett_in_the_mail_is_a_problem():
    # code turns the sentence's ß into ss; a profile field can still carry one
    c = contact()
    sender = SENDER.model_copy(update={"name": "Lena Großmann"})
    body = assemble(c, GOOD, link=LINK, sender=sender, inv=INVITATION)
    d = make(GOOD, body=body)
    profile = PROFILE.model_copy(update={"sender": sender})
    assert any("ss, never ß" in p for p in problems(d, c, profile))


def test_a_tampered_frame_fails():
    d = make(GOOD)
    no_routing = d.model_copy(update={"body": d.body.split("\n\n", 1)[1]})
    assert any("frame" in p for p in found(no_routing))


def test_a_model_subject_is_rejected():
    assert any("subject" in p for p in found(make(GOOD, subject="Kurze Umfrage")))


def test_a_subject_with_a_link_is_a_problem():
    c = contact()
    sender = SENDER.model_copy(update={"school_short": "OST www.evil.example"})
    profile = PROFILE.model_copy(update={"sender": sender})
    body = assemble(c, GOOD, link=LINK, sender=sender, inv=INVITATION)
    d = make(GOOD, body=body, subject=subject(c, sender, INVITATION))
    assert any("subject" in p and "link" in p for p in problems(d, c, profile))


def test_a_mail_of_1300_characters_is_long_enough():
    # frame@1 alone is about 1,000 characters; the limit moved from 1,200
    body = make(GOOD).body
    padded = make(GOOD, body=body + " " * (1300 - len(body)))
    assert not any("longer than" in p for p in found(padded))


def test_a_kurz_draft_passes_and_is_checked_as_kurz():
    c = contact()
    short = assemble(c, GOOD, link=LINK, sender=SENDER, inv=INVITATION, short=True)
    kurz = make(GOOD, body=short, arm="kurz")
    assert found(kurz) == []
    # the same body claimed as "voll" is not what the frame writes
    assert any("frame" in p for p in found(make(GOOD, body=short)))


def test_the_five_example_mails_pass():
    import json
    from pathlib import Path

    from company_reach.profile import Invitation, Sender
    from company_reach.tools.invitation import survey_link

    examples = json.loads(
        (Path(__file__).parent / "fixtures/invitation/examples.json").read_text()
    )
    profile = Profile(
        goal="g",
        survey_url=examples["survey_url"],
        sender=Sender(**examples["sender"]),
        invitation=Invitation(**examples["invitation"]),
    )
    link = survey_link(examples["survey_url"], examples["uid"])
    for mail in examples["mails"]:
        c = Contact(**mail["contact"])
        d = Draft(
            subject=mail["subject"],
            body=mail["body"],
            model_text=mail["sentence"],
            link=link,
            mailto_fits=True,
            frame_version=FRAME_VERSION,
        )
        assert problems(d, c, profile) == [], mail["kind"]


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


def check_outcome(settings) -> str | None:
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute("select problems from drafts").fetchone()[0]
    finally:
        conn.close()


async def test_a_passing_draft_is_recorded_as_checked(db_settings):
    store(db_settings, make(GOOD))
    assert check_outcome(db_settings) is None  # written, not yet checked
    await check_draft(state(make(GOOD)), settings=db_settings, redraft=Redraft())
    assert check_outcome(db_settings) == ""


async def test_a_raising_redraft_leaves_no_draft(db_settings):
    """The first draft failed; the second attempt raised (the endpoint went
    down). The first draft must not stay behind, one click from being sent,
    while the error travels to the result row."""

    async def down(state, *, settings):
        raise LlmError("endpoint down while redrafting")

    first = make(EVIL)
    store(db_settings, first)
    with pytest.raises(LlmError):
        await check_draft(state(first), settings=db_settings, redraft=down)
    assert stored_drafts(db_settings) == 0


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
