from company_reach.models import CompanyRecord


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
