"""The review page's data: one card per company of a run, and whether it
may be sent. No HTTP here; the page's own tests are in test_review_app."""

from datetime import date
from pathlib import Path

import pytest
from fictional_profile import INVITATION, SENDER
from review_seed import HOLD, RUN, SEND, SKIP, SURVEY, seed

from company_reach.models import Contact
from company_reach.profile import Profile
from company_reach.review.cards import first_undecided, load_cards
from company_reach.tools.db import connect, record_contact, record_decision, suppress


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    seed(path)
    return path


def cards(db: Path, *, approved: bool = True, survey_url: str = SURVEY):
    with connect(db) as conn:
        return load_cards(conn, RUN, survey_url=survey_url, sending_approved=approved)


def by_uid(db: Path, **kw):
    return {c.uid: c for c in cards(db, **kw)}


def test_send_comes_first_then_hold_then_skip(db: Path):
    assert [c.uid for c in cards(db)] == [SEND, HOLD, SKIP]


def test_a_sendable_card_carries_what_the_reviewer_needs(db: Path):
    card = by_uid(db)[SEND]
    assert (card.name, card.legal_form, card.seat) == (
        "Muster Metallbau AG",
        "AG",
        "Musterstadt",
    )
    assert card.what == "Makes steel stair railings."
    assert card.site_url == "https://muster-metallbau.ch/"
    assert card.evidence_label == "UID matches the register"
    assert [a.email for a in card.contact.addresses] == [
        "info@muster-metallbau.ch",
        "studio@agentur.example",
    ]
    assert card.draft.subject == "Für Frau Muster: Masterarbeit an der OST"
    assert card.link_length > 0
    assert card.send_block is None


def test_sending_is_locked_until_ethics_approval_is_recorded(db: Path):
    block = by_uid(db, approved=False)[SEND].send_block
    assert "SENDING_APPROVED" in block


def test_a_placeholder_survey_link_cannot_be_sent(tmp_path: Path):
    path = tmp_path / "p.db"
    seed(path, link=f"https://survey.example/?c={SEND}&l=de")
    with connect(path) as conn:
        [card] = [
            c
            for c in load_cards(
                conn, RUN, survey_url="https://survey.example", sending_approved=True
            )
            if c.uid == SEND
        ]
    assert "placeholder" in card.send_block


def test_a_draft_linking_to_an_old_survey_url_cannot_be_sent(db: Path):
    block = by_uid(db, survey_url="https://survey.test/new-form")[SEND].send_block
    assert "survey_url" in block


def test_a_held_company_has_no_draft_and_says_why(db: Path):
    card = by_uid(db)[HOLD]
    assert card.draft is None
    assert "another domain" in card.send_block


def test_a_hostile_site_url_is_never_offered_as_a_link(db: Path):
    assert by_uid(db)[HOLD].site_url is None


def test_a_company_without_a_site_shows_the_searches_tried(db: Path):
    card = by_uid(db)[SKIP]
    assert card.site_url is None
    assert card.queries == ['"Exempel Druck" Musterstadt', "Exempel Druck Impressum"]


def test_a_decided_company_cannot_be_sent_again(db: Path):
    with connect(db) as conn:
        record_decision(conn, SEND, "sent", address="info@muster-metallbau.ch")
    card = by_uid(db)[SEND]
    assert card.decision == "sent"
    assert "already" in card.send_block.lower()


def test_a_suppressed_company_cannot_be_sent(db: Path):
    with connect(db) as conn:
        suppress(conn, SEND, reason="never again")
    assert "never" in by_uid(db)[SEND].send_block.lower()


def test_the_first_undecided_card_is_where_the_page_opens(db: Path):
    assert first_undecided(cards(db)) == 0
    # with no mail waiting, the run opens at its first card, not at a company
    # the run did not recommend (issue #54)
    with connect(db) as conn:
        record_decision(conn, SEND, "skipped", note="Not a fit")
    assert first_undecided(cards(db)) == 0


def test_a_contact_written_before_the_address_list_offers_its_one_address(db: Path):
    # runs from before M7 stored the chosen address only
    with connect(db) as conn:
        conn.execute("update contacts set addresses = null where uid = ?", (SEND,))
    card = by_uid(db)[SEND]
    assert [(a.email, a.kind) for a in card.contact.addresses] == [
        ("info@muster-metallbau.ch", "generic")
    ]


def test_a_no_site_card_shows_the_search_log(db: Path):
    """#20: a "no website" says what it rests on — each query, whom it was
    put to, and what came back."""
    card = by_uid(db)[SKIP]
    assert card.searches == [
        '"Exempel Druck" Musterstadt — searxng · 0 results · down: brave, duckduckgo',
        "Exempel Druck Impressum — searxng · error: SearXNG answered HTTP 503",
        "Exempel Druck Impressum — brave · 1 result",
    ]


def test_a_failed_draft_is_not_sendable(db: Path):
    # the card gates Send on the latest check, not on a row existing
    with connect(db) as conn:
        conn.execute(
            "update drafts set problems = ? where uid = ?",
            ("the sentence uses 'wertvoll'", SEND),
        )
    block = by_uid(db)[SEND].send_block
    assert "failed its checks" in block
    assert "wertvoll" in block


def test_an_unchecked_draft_is_not_sendable(db: Path):
    # written, and the run stopped before check_draft looked at it
    with connect(db) as conn:
        conn.execute("update drafts set problems = null where uid = ?", (SEND,))
    assert "never checked" in by_uid(db)[SEND].send_block


def test_a_draft_of_an_older_frame_is_not_sendable(db: Path):
    # every draft stored before frame@1 carries the old frame
    with connect(db) as conn:
        conn.execute("update drafts set frame_version = null where uid = ?", (SEND,))
    assert "older frame" in by_uid(db)[SEND].send_block


def test_fallback_greeting_is_flagged(tmp_path: Path):
    # a masculine role on a site proposes no salutation, so the greeting is
    # the full name and the reviewer is asked to check it
    path = tmp_path / "g.db"
    seed(path, role="Gründer")
    with connect(path) as conn:
        [card] = [
            c
            for c in load_cards(conn, RUN, survey_url=SURVEY, sending_approved=True)
            if c.uid == SEND
        ]
    assert card.check_salutation
    assert card.salutation is None
    assert card.can_choose_salutation


def test_a_salutation_from_the_role_is_flagged_and_says_so(db: Path):
    # a role only proposes: every value that is not the reviewer's choice or
    # a full "Frau Anna Muster" on the page is checked by the reviewer
    card = by_uid(db)[SEND]  # "Inhaberin"
    assert card.check_salutation
    assert card.salutation == "Frau"
    assert card.salutation_origin == "from the role"
    assert card.can_choose_salutation


@pytest.mark.parametrize(
    ("origin", "label", "flagged"),
    [
        ("reviewer", "your choice", False),
        ("page", "written on the page", False),
        ("page-surname", "on the page, surname only", True),
    ],
)
def test_the_card_shows_where_the_salutation_came_from(
    db: Path, origin, label, flagged
):
    with connect(db) as conn:
        conn.execute(
            "update contacts set salutation = 'Frau', salutation_origin = ?"
            " where uid = ?",
            (origin, SEND),
        )
    card = by_uid(db)[SEND]
    assert card.salutation_origin == label
    assert card.check_salutation is flagged


def test_nobody_named_offers_no_salutation(db: Path):
    assert not by_uid(db)[HOLD].can_choose_salutation


# --- a draft is checked against today's profile and contact -----------------

PROFILE = Profile(goal="g", survey_url=SURVEY, sender=SENDER, invitation=INVITATION)


def with_profile(db: Path, profile: Profile):
    with connect(db) as conn:
        return {
            c.uid: c
            for c in load_cards(
                conn, RUN, survey_url=SURVEY, sending_approved=True, profile=profile
            )
        }


def test_a_draft_that_matches_the_profile_stays_sendable(db: Path):
    assert with_profile(db, PROFILE)[SEND].send_block is None


@pytest.mark.parametrize(
    "change",
    [
        {"invitation": INVITATION.model_copy(update={"closes": date(2026, 11, 13)})},
        {"sender": SENDER.model_copy(update={"supervisor": ""})},
    ],
    ids=["closes", "supervisor"],
)
def test_a_profile_change_makes_the_draft_unsendable(db: Path, change):
    """Review: only a changed survey_url was caught. A mail promising the old
    closing date, or naming a supervisor who withdrew, is just as stale."""
    block = with_profile(db, PROFILE.model_copy(update=change))[SEND].send_block
    assert "profile" in block
    assert "redraft" in block


def test_a_newer_contact_makes_the_draft_unsendable(db: Path):
    # a retry found someone else; the draft still greets the old contact
    with connect(db) as conn:
        record_contact(
            conn,
            RUN,
            SEND,
            Contact(
                name="Beat Beispiel",
                role="Geschäftsführer",
                email="info@muster-metallbau.ch",
                email_kind="generic",
                source="site",
            ),
        )
    block = with_profile(db, PROFILE)[SEND].send_block
    assert "contact" in block
    assert "redraft" in block
