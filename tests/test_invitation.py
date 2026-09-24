"""The parts of an invitation the model never writes: the survey link and
the data-protection sentence. Both are added by code after drafting, so that
page text cannot steer either of them."""

import pytest

from company_reach.tools.invitation import (
    InvitationError,
    privacy_sentence,
    survey_link,
    uid_is_valid,
)

URL = "https://survey.example/form"
# CHE-000.000.046: the check digit of 00000004 under the UID weights is 6
UID = "CHE000000046"


# --- the UID -----------------------------------------------------------------


def test_a_register_uid_passes_its_check_digit():
    assert uid_is_valid("CHE-000.000.046")
    assert uid_is_valid("CHE-123.456.788")


def test_a_wrong_check_digit_fails():
    assert not uid_is_valid("CHE-000.000.047")


def test_the_check_digit_ten_never_exists():
    # 11 - (sum mod 11) = 10 has no digit, so such a number is never issued:
    # 00000003 weighs 3 x 4 = 12, and 11 - (12 mod 11) = 10
    assert not any(uid_is_valid(f"CHE00000003{d}") for d in "0123456789")


def test_anything_but_che_and_nine_digits_fails():
    assert not uid_is_valid("SMOKE")
    assert not uid_is_valid("CHE-000.000.04")
    assert not uid_is_valid("DEU000000046")


# --- the link ----------------------------------------------------------------


@pytest.mark.parametrize(
    "written", ["CHE-000.000.046", "CHE000000046", "CHE 000 000 046"]
)
def test_every_way_of_writing_the_uid_gives_the_same_link(written: str):
    assert survey_link(URL, written) == f"{URL}/?c={UID}&l=de"


def test_a_trailing_slash_is_not_doubled():
    assert survey_link(f"{URL}/", UID) == f"{URL}/?c={UID}&l=de"


def test_english_is_the_other_language():
    assert survey_link(URL, UID, lang="en").endswith("&l=en")


def test_no_third_language():
    with pytest.raises(InvitationError, match="lang"):
        survey_link(URL, UID, lang="fr")


def test_an_invalid_uid_never_reaches_a_link():
    # the survey opens its follow-up questions only for a valid UID; an
    # invitation carrying a broken one would look fine and record nothing
    with pytest.raises(InvitationError, match="check digit"):
        survey_link(URL, "CHE-000.000.047")


def test_the_smoke_tag_is_never_an_invitation():
    # reserved for the survey's own smoke test
    with pytest.raises(InvitationError):
        survey_link(URL, "SMOKE")


def test_no_survey_url_is_an_error():
    with pytest.raises(InvitationError, match="survey_url"):
        survey_link("", UID)


# --- the data-protection sentence --------------------------------------------


def test_the_sentence_names_the_site_as_the_source():
    sentence = privacy_sentence("site")
    assert "von Ihrer Website" in sentence
    assert "lösche ich sie" in sentence


def test_the_sentence_names_shab_as_the_source():
    assert "Schweizerischen Handelsamtsblatt (SHAB)" in privacy_sentence("shab")
