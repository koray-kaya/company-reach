"""Personal links: codes that cannot be misread, links in the survey's
shape, and one key per profile however it was copied."""

from pathlib import Path

import pytest

from company_reach.invites import (
    CODE,
    code_in,
    new_code,
    personal_link,
    profile_key,
)
from company_reach.tools.db import connect, init_db
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
