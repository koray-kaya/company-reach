from company_reach.models import CompanyRecord
from company_reach.screen import head_clause, screen_reason


def make(name="Muster AG", purpose="Betrieb einer Schreinerei.") -> CompanyRecord:
    return CompanyRecord(
        uid="CHE000000001",
        name=name,
        legal_form="0106",
        municipality="3203",
        street="Musterstrasse 1",
        postal_code="9000",
        city="St. Gallen",
        purpose=purpose,
        purpose_head=head_clause(purpose),
    )


def test_head_clause_drops_boilerplate_tail():
    p = (
        "Betrieb einer Schreinerei. Die Gesellschaft kann Zweigniederlassungen "
        "errichten und Grundstücke erwerben, belasten und veräussern."
    )
    assert head_clause(p) == "Betrieb einer Schreinerei."


def test_head_clause_sie_kann_variant():
    p = "Handel mit Möbeln. Sie kann Liegenschaften erwerben."
    assert head_clause(p) == "Handel mit Möbeln."


def test_head_clause_without_tail_is_unchanged():
    assert head_clause("Betrieb einer Schreinerei.") == "Betrieb einer Schreinerei."


def test_liquidation_is_dropped():
    assert screen_reason(make(name="Muster AG in Liquidation")) == "in liquidation"


def test_property_only_is_dropped():
    r = make(
        purpose=(
            "Erwerb, Halten und Verwaltung von Liegenschaften. "
            "Die Gesellschaft kann Zweigniederlassungen errichten."
        )
    )
    assert screen_reason(r) == "property only"


def test_property_plus_operating_signal_is_kept():
    r = make(purpose="Herstellung von Fenstern sowie Verwaltung von Liegenschaften.")
    assert screen_reason(r) is None


def test_dienstleistung_alone_is_not_an_operating_signal():
    r = make(
        purpose="Dienstleistungen im Immobilienbereich, Verwaltung von Liegenschaften."
    )
    assert screen_reason(r) == "property only"
