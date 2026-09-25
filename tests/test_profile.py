from datetime import date

import pytest

from company_reach.errors import ProfileError
from company_reach.profile import goal_hash, load_profile

TOML = """
goal = "Swiss companies in manufacturing or technical services."
about_me = "A master's student writing a thesis."
"""


def test_reads_goal_and_about_me(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(TOML)
    profile = load_profile(path)
    assert profile.goal.startswith("Swiss companies")
    assert "thesis" in profile.about_me


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(ProfileError, match="profile.toml"):
        load_profile(tmp_path / "profile.toml")


def test_empty_goal_is_an_error(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('goal = ""\nabout_me = "x"\n')
    with pytest.raises(ProfileError, match="goal"):
        load_profile(path)


def test_hash_ignores_formatting_but_not_wording():
    a = "Swiss companies in manufacturing."
    assert goal_hash(a) == goal_hash("  Swiss   companies in   manufacturing.  ")
    assert goal_hash(a) == goal_hash("Swiss companies in Manufacturing.")
    assert goal_hash(a) != goal_hash("Swiss companies in trade.")


def test_hash_is_short_and_stable():
    h = goal_hash("anything")
    assert len(h) == 12
    assert h == goal_hash("anything")


def test_reads_the_survey_url(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(TOML + 'survey_url = "https://survey.example/form"\n')
    assert load_profile(path).survey_url == "https://survey.example/form"


def test_the_survey_url_is_optional_until_drafting(tmp_path):
    # scoring and site finding do not need it; doctor and draft do
    path = tmp_path / "profile.toml"
    path.write_text(TOML)
    assert load_profile(path).survey_url == ""


def test_a_survey_url_that_is_not_https_is_an_error(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(TOML + 'survey_url = "http://survey.example/form"\n')
    with pytest.raises(ProfileError, match="https"):
        load_profile(path)


# --- [sender] and [invitation] (frame@1) --------------------------------------

FULL = (
    TOML
    + """survey_url = "https://survey.example/form"

[sender]
name = "Lena Brunner"
affiliation = "Masterstudentin, OST Ostschweizer Fachhochschule"
school_short = "OST"
place = "St. Gallen"
supervisor = "Prof. Dr. Hans Vorbild"

[invitation]
topic = "wie KMU zu Kunden und Lieferanten kommen"
minutes = 15
closes = 2026-10-30
offer_results = true
no_login = true
"""
)


def test_sender_and_invitation_sections_load(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(FULL)
    profile = load_profile(path)
    assert profile.sender.name == "Lena Brunner"
    assert profile.sender.school_short == "OST"
    assert profile.sender.place == "St. Gallen"
    assert profile.sender.supervisor == "Prof. Dr. Hans Vorbild"
    inv = profile.invitation
    assert inv.topic == "wie KMU zu Kunden und Lieferanten kommen"
    assert inv.minutes == 15
    assert (inv.offer_results, inv.no_login) == (True, True)
    # every sentence whose fact is not confirmed stays off by default
    assert (inv.reminder, inv.experiment) == (False, False)
    assert inv.sign_in_body is True
    assert profile.drafting_gaps() == []


def test_closes_is_read_as_a_date(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(FULL)
    assert load_profile(path).invitation.closes == date(2026, 10, 30)


def test_a_profile_without_sender_still_loads(tmp_path):
    # scoring and site finding need none of it; drafting refuses without it
    path = tmp_path / "profile.toml"
    path.write_text(TOML)
    profile = load_profile(path)
    assert profile.sender.name == ""
    assert profile.invitation.closes is None
    assert profile.invitation.minutes == 15
    assert profile.drafting_gaps() == [
        "survey_url",
        "sender.name",
        "sender.affiliation",
        "sender.school_short",
        "invitation.topic",
    ]


def test_a_misspelt_field_is_named(tmp_path):
    # a typo would otherwise vanish, and the mail would lack the field
    path = tmp_path / "profile.toml"
    path.write_text(TOML + '[sender]\nschool = "OST"\n')
    with pytest.raises(ProfileError, match="school"):
        load_profile(path)


def test_a_wrong_type_is_a_profile_error(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(TOML + '[invitation]\nminutes = "fifteen"\n')
    with pytest.raises(ProfileError, match="minutes"):
        load_profile(path)


def test_the_example_profile_loads_and_names_no_university():
    from pathlib import Path

    example = Path(__file__).parents[1] / "profile.toml.example"
    profile = load_profile(example)
    assert profile.drafting_gaps() == []
    # a school named in about_me reached drafts as an invented "Universität",
    # and a denial of selling made them sound like selling
    assert "universit" not in profile.about_me.lower()
    assert "nothing to sell" not in profile.about_me.lower()
