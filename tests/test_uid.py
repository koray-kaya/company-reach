"""UIDs found on a web page, not UIDs from the register.

The register is authoritative — `CompanyRecord` normalises its UID and asks
no further questions. A nine-digit number sitting in page text is a different
thing: it might be an order number, a phone number or the VAT id of the
group's parent. The check digit is what separates a real UID from a number
that merely looks like one, which is why tier 1 can be decisive.
"""

import pytest

from company_reach.tools.uid import find_uids, is_valid_uid, normalise_uid, uid_match

VALID = "CHE000000046"
ALSO_VALID = "CHE111111118"
BAD_DIGIT = "CHE000000042"


@pytest.mark.parametrize(
    "written",
    [
        "CHE-000.000.046",
        "CHE000000046",
        "CHE 000 000 046",
        "CHE-000.000.046 MWST",
        "CHE-000.000.046 TVA",
        "CHE-000.000.046 IVA",
        "CHE-000.000.046 VAT",
        "CHE-000.000.046 HR",
    ],
)
def test_every_spelling_normalises_to_one_form(written: str):
    assert normalise_uid(written) == VALID


def test_the_check_digit_is_verified():
    assert is_valid_uid(VALID)
    assert not is_valid_uid(BAD_DIGIT)


def test_a_number_that_only_looks_like_a_uid_is_rejected():
    """Weights 5,4,3,2,7,6,5,4; sum mod 11; 11 counts as 0 and 10 is invalid."""
    assert not is_valid_uid("CHE123456789")


def test_uids_are_found_inside_page_text():
    text = "Kontakt\nMuster Metallbau\nCHE-000.000.046 MWST\nTelefon 044 000 00 00"
    assert find_uids(text) == [VALID]


def test_invalid_candidates_are_not_returned():
    text = "Bestellnummer CHE-000.000.042, Ust-Id CHE-111.111.118"
    assert find_uids(text) == [ALSO_VALID]


def test_a_matching_uid_is_a_match():
    assert uid_match(VALID, "Impressum CHE-000.000.046") == "match"


def test_a_different_valid_uid_is_reported_as_different():
    """The group's parent entity. A useful negative signal, not 'no match' —
    it says the page belongs to a related but different company."""
    assert uid_match(VALID, f"Impressum {ALSO_VALID}") == "different"


def test_no_uid_on_the_page_is_neither():
    assert uid_match(VALID, "Impressum ohne Nummer") == "absent"
