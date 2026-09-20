import pytest
from pydantic import ValidationError

from company_reach.models import CompanyRecord, CompanyResult


def test_uid_is_normalised_to_digits_only():
    r = CompanyRecord(
        uid="CHE-000.000.042",
        name="X AG",
        legal_form="0106",
        municipality="3203",
        purpose="",
        purpose_head="",
    )
    assert r.uid == "CHE000000042"
    assert r.uid_dotted == "CHE-000.000.042"


def test_company_result_rejects_an_unknown_error_kind():
    with pytest.raises(ValidationError):
        CompanyResult(uid="CHE000000042", error_kind="timeout")


def test_company_result_is_empty_until_the_child_decides():
    r = CompanyResult(uid="CHE000000042")
    assert r.recommendation is None
    assert r.error_kind is None


def test_company_result_takes_a_recommendation():
    r = CompanyResult(uid="CHE000000042", recommendation="send", reason="fits")
    assert r.recommendation == "send"
