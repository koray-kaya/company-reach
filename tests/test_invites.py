"""Personal links: codes that cannot be misread, links in the survey's
shape, and one key per profile however it was copied."""

from pathlib import Path

import pytest

from company_reach.invites import (
    CODE,
    InviteError,
    NewInvite,
    check,
    clear_people,
    code_in,
    contact_log,
    invite_for,
    new_code,
    personal_link,
    profile_key,
    record_invite,
    remove_invite,
)
from company_reach.models import CompanyRecord
from company_reach.tools.db import (
    connect,
    init_db,
    record_decision,
    suppress,
    upsert_companies,
)
from company_reach.tools.invitation import InvitationError

SURVEY = "https://survey.test/form"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    init_db(path)
    return path


def test_a_code_is_p_and_six_characters_that_cannot_be_misread(db):
    with connect(db) as conn:
        code = new_code(conn)
    assert CODE.match(code)
    assert not set(code[2:]) & set("01OIL")


def test_a_code_already_given_is_never_given_again(db):
    with connect(db) as conn:
        conn.execute(
            "insert into invites (code, person, company, channel, created_at)"
            " values ('P-222222', 'Anna Muster', 'Muster AG', 'linkedin', '2026-09-27')"
        )
        picks = iter("222222" + "333333")
        assert new_code(conn, choice=lambda _: next(picks)) == "P-333333"


def test_a_personal_link_has_the_survey_links_shape():
    expected = "https://survey.test/form/?c=P-7K3Q9X&l=de"
    assert personal_link(SURVEY, "P-7K3Q9X") == expected
    assert personal_link(SURVEY + "/", "P-7K3Q9X", lang="en").endswith(
        "?c=P-7K3Q9X&l=en"
    )


@pytest.mark.parametrize("code", ["P-7K3Q9", "P-7K3Q9O", "CHE000000046", ""])
def test_a_personal_link_refuses_what_is_not_a_code(code):
    with pytest.raises(InvitationError):
        personal_link(SURVEY, code)


@pytest.mark.parametrize(
    "text",
    ["P-7K3Q9X", " p-7k3q9x ", "https://survey.test/?c=P-7K3Q9X&l=de"],
)
def test_a_code_is_read_typed_or_from_a_pasted_link(text):
    assert code_in(text) == "P-7K3Q9X"


@pytest.mark.parametrize("text", ["CHE000000046", "Anna Muster", "P-0000000"])
def test_what_is_not_a_code_reads_as_none(text):
    assert code_in(text) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/Anna-Muster/?utm_source=share",
        "linkedin.com/in/anna-muster",
        "http://ch.linkedin.com/in/anna-muster/",
    ],
)
def test_one_profile_is_one_key_however_it_was_copied(url):
    assert profile_key(url) == "linkedin.com/in/anna-muster"


@pytest.mark.parametrize("url", [None, "", "   "])
def test_no_profile_is_no_key(url):
    assert profile_key(url) is None


UID = "CHE000000046"  # fictional, valid check digit


def anna(**changes) -> NewInvite:
    fields = {
        "person": "Anna Muster",
        "company": "Muster Stahlbau AG",
        "profile": "https://www.linkedin.com/in/anna-muster/",
    }
    return NewInvite(**(fields | changes))


def add_company(conn, uid=UID, name="Muster Stahlbau AG"):
    upsert_companies(
        conn,
        [
            CompanyRecord(
                uid=uid,
                name=name,
                legal_form="0106",
                municipality="3203",
                purpose="Herstellung von Treppen.",
                purpose_head="Herstellung von Treppen.",
            )
        ],
        "r1",
    )


def test_a_first_contact_has_nothing_to_warn_about(db):
    with connect(db) as conn:
        assert check(conn, anna()) == (None, [])


def test_the_company_number_is_found_by_its_exact_name(db):
    with connect(db) as conn:
        add_company(conn)
        uid, _ = check(conn, anna(company="  muster stahlbau ag "))
    assert uid == UID


def test_two_companies_of_one_name_give_no_number(db):
    with connect(db) as conn:
        add_company(conn)
        add_company(conn, uid="CHE123456788")
        assert check(conn, anna())[0] is None


def test_a_typed_number_with_a_wrong_check_digit_is_refused(db):
    with connect(db) as conn, pytest.raises(InviteError, match="check digit"):
        check(conn, anna(uid="CHE-000.000.047"))


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"person": "  "}, "name is missing"),
        ({"company": ""}, "Which company"),
        ({"channel": "fax"}, "Unknown channel"),
        ({"note": "x" * 501}, "longer than 500"),
    ],
)
def test_an_incomplete_link_is_refused(db, changes, words):
    with connect(db) as conn, pytest.raises(InviteError, match=words):
        check(conn, anna(**changes))


def test_a_company_the_tool_mailed_is_asked_about(db):
    with connect(db) as conn:
        record_decision(
            conn,
            UID,
            "sent",
            address="info@muster.example",
            decided_at="2026-09-20T09:00:00+00:00",
        )
        uid, notes = check(conn, anna(uid=UID))
    assert uid == UID
    assert notes == ["The tool mailed this company on 2026-09-20."]


def test_a_second_person_at_the_company_is_asked_about(db):
    with connect(db) as conn:
        record_invite(conn, anna(), uid=None)
        _, notes = check(conn, anna(person="Beat Beispiel", profile=""))
    assert len(notes) == 1
    assert notes[0].startswith("Anna Muster at this company got a personal link on ")


def test_a_person_with_a_link_gets_no_second_one(db):
    with connect(db) as conn:
        first = record_invite(conn, anna(), uid=None)
        with pytest.raises(InviteError, match=first.code):
            check(conn, anna(profile="linkedin.com/in/Anna-Muster"))


def test_a_company_on_the_never_again_list_gets_no_link(db):
    with connect(db) as conn:
        suppress(conn, UID, reason="never")
        with pytest.raises(InviteError, match="never-again"):
            check(conn, anna(uid=UID))


def test_a_recorded_link_keeps_the_person_and_one_profile_key(db):
    with connect(db) as conn:
        made = record_invite(conn, anna(note="  met at a fair "), uid=UID)
        again = invite_for(conn, made.code)
    assert again == made
    assert (made.person, made.uid, made.channel) == ("Anna Muster", UID, "linkedin")
    assert made.profile == "linkedin.com/in/anna-muster"
    assert made.note == "met at a fair"


def test_the_log_holds_links_and_mails_newest_first_with_their_answers(db):
    with connect(db) as conn:
        record_decision(
            conn,
            UID,
            "sent",
            address="info@muster.example",
            decided_at="2026-09-20T09:00:00+00:00",
        )
        made = record_invite(conn, anna(), uid=None)
        conn.execute(
            "insert into responses (uid, started_at, completed_at, imported_at)"
            " values (?, '2026-09-28T10:00:00', '2026-09-28T10:12:00', 'x')",
            (made.code,),
        )
        log = contact_log(conn, SURVEY)
    assert [r.channel for r in log] == ["LinkedIn", "Mail from this tool"]
    link, mail = log
    assert (link.person, link.answer, link.removable) == (
        "Anna Muster",
        "completed",
        False,
    )
    assert link.link == f"{SURVEY}/?c={made.code}&l=de"
    assert (mail.tag, mail.answer, mail.company) == (UID, "not yet", UID)
    assert mail.link == f"{SURVEY}/?c={UID}&l=de"


def test_a_mail_taken_back_is_not_in_the_log(db):
    with connect(db) as conn:
        sent_id = record_decision(conn, UID, "sent", address="info@muster.example")
        record_decision(conn, UID, "not_sent", reverses=sent_id)
        assert contact_log(conn, SURVEY) == []


def test_without_a_survey_address_the_log_has_no_links(db):
    with connect(db) as conn:
        record_invite(conn, anna(), uid=None)
        assert contact_log(conn, None)[0].link is None


def test_an_unanswered_link_can_be_removed_an_answered_one_not(db):
    with connect(db) as conn:
        a = record_invite(conn, anna(), uid=None)
        b = record_invite(conn, anna(person="Beat Beispiel", profile=""), uid=None)
        conn.execute(
            "insert into responses (uid, started_at, imported_at)"
            " values (?, '2026-09-28', 'x')",
            (b.code,),
        )
        assert remove_invite(conn, a.code) is True
        assert remove_invite(conn, b.code) is False
        assert invite_for(conn, a.code) is None
        assert invite_for(conn, b.code) is not None


def test_clearing_people_keeps_the_row(db):
    with connect(db) as conn:
        made = record_invite(conn, anna(note="n"), uid=UID)
        assert clear_people(conn, [made.code]) == 1
        assert clear_people(conn, [made.code]) == 0  # nothing left to clear
        kept = invite_for(conn, made.code)
    assert (kept.person, kept.profile, kept.note) == (None, None, None)
    assert (kept.company, kept.uid) == ("Muster Stahlbau AG", UID)
