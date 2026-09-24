"""SHAB: who the register says held a role, and when it said so.

The fixtures under `fixtures/shab/` are fictional companies and people in
the shape of real notices — the repository is public, and a named person in
a register notice is still personal data.
"""

from pathlib import Path

import httpx
import pytest
import respx

from company_reach.errors import ShabError
from company_reach.settings import Settings
from company_reach.tools.shab import parse_persons, persons

FIXTURES = Path(__file__).parent / "fixtures/shab"
UID = "CHE000000004"
DOTTED = "CHE-000.000.004"


def text_of(name: str) -> str:
    xml = (FIXTURES / name).read_text()
    return xml.split("<publicationText>")[1].split("</publicationText>")[0]


def listing(*ids: str, total: int | None = None) -> dict:
    return {
        "content": [{"meta": {"id": i}} for i in ids],
        "total": len(ids) if total is None else total,
    }


# --- the parser --------------------------------------------------------------


def test_a_german_mutation_yields_departed_and_current_people():
    found = parse_persons(text_of("de_mutation.xml"))
    assert [(p.name, p.departed) for p in found] == [
        ("Anna Beispiel", True),
        ("Peter Muster", False),
        ("Lena Probst", False),
    ]


def test_the_role_is_kept_and_everything_else_in_the_entry_is_dropped():
    anna, peter, lena = parse_persons(text_of("de_mutation.xml"))
    assert anna.role == "Präsidentin des Verwaltungsrates"
    # origin, residence, signature, shares and the "[bisher: ...]" history
    # are all in the entry; only the function survives
    assert peter.role == "Gesellschafter und Geschäftsführer"
    # a citizenship in place of "von <Heimatort>"
    assert lena.role == "Mitglied des Verwaltungsrates"


def test_the_audit_firm_is_a_company_not_a_person():
    names = [p.name for p in parse_persons(text_of("de_mutation.xml"))]
    assert not any("Treuhand" in n for n in names)


def test_companies_and_associations_in_a_block_are_not_people():
    # found live: an audit firm under the pre-UID register number, and an
    # unregistered association holding a seat
    text = (
        "Muster AG, in St. Gallen, CHE-000.000.004, Aktiengesellschaft. "
        "Eingetragene Personen neu oder mutierend: Treuhand Beispiel AG "
        "(CH-320.3.000.000-0), in St. Gallen, Revisionsstelle; Beispiel "
        "Turnverein (nicht im Handelsregister eingetragener Verein), in Wil SG, "
        "mit Kollektivunterschrift zu zweien; Muster, Eva, von Wil SG, in Wil "
        "SG, Präsidentin des Verwaltungsrates, mit Einzelunterschrift."
    )
    assert [p.name for p in parse_persons(text)] == ["Eva Muster"]


def test_a_second_place_of_origin_is_not_a_role():
    # found live: "von <A>, <B> und <C>" lists several places of origin
    text = (
        "Muster AG, in St. Gallen, CHE-000.000.004, Aktiengesellschaft. "
        "Eingetragene Personen neu oder mutierend: Muster, Eva, von Wil SG, "
        "Gossau SG und Flawil, in Wil SG, Mitglied des Verwaltungsrates, mit "
        "Kollektivunterschrift zu zweien."
    )
    [eva] = parse_persons(text)
    assert eva.role == "Mitglied des Verwaltungsrates"


def test_a_new_entry_uses_the_shorter_header():
    [anna] = parse_persons(text_of("new_entry.xml"))
    assert (anna.name, anna.role, anna.departed) == (
        "Anna Beispiel",
        "Präsidentin des Verwaltungsrates",
        False,
    )


def test_a_notice_without_a_person_block_names_nobody():
    assert parse_persons(text_of("no_block.xml")) == []


def test_a_signature_without_a_function_has_no_role():
    [marco] = parse_persons(text_of("no_function.xml"))
    assert (marco.name, marco.role) == ("Marco Keller", None)


def test_french_headers_and_the_comma_less_name_form():
    # FR writes "Surname Firstnames" with no comma, so the parts cannot be
    # told apart; the name is kept as the notice writes it
    dupont, martin = parse_persons(text_of("fr_canton.xml"))
    assert (dupont.name, dupont.role, dupont.departed) == (
        "Dupont Jean Marc",
        "administrateur",
        True,
    )
    assert (martin.name, martin.role, martin.departed) == (
        "Martin Claire Sophie",
        "présidente",
        False,
    )


def test_german_headers_used_by_a_bilingual_registry():
    text = (
        "Muster Holz AG, in Murten, CHE-000.000.006 (SHAB vom 01.01.2025). "
        "Gelöschte Personen: Beispiel Hans Peter, Direktor, Kollektivunterschrift "
        "zu zweien. Neu eingetragene Person: Muster Eva, von Bern, in Murten, "
        "Kollektivunterschrift zu zweien."
    )
    found = parse_persons(text)
    assert [(p.name, p.role, p.departed) for p in found] == [
        ("Beispiel Hans Peter", "Direktor", True),
        ("Muster Eva", None, False),
    ]


def test_prose_notices_without_a_header_are_not_parsed():
    # VD, GE and NE write persons as prose; M6 reads headed blocks only
    text = (
        "Exemple Sàrl, à Nyon, CHE-000.000.007 (FOSC du 01.01.2025). "
        "Signature individuelle est conférée à Dupont Luc, de Genève, à Nyon, "
        "directeur."
    )
    assert parse_persons(text) == []


# --- the API -----------------------------------------------------------------


@pytest.fixture
def shab_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"shab_url": "https://shab.test/api/v1"})


def mock_xml(publication_id: str, fixture: str) -> respx.Route:
    return respx.get(
        f"https://shab.test/api/v1/publications/{publication_id}/xml"
    ).mock(return_value=httpx.Response(200, text=(FIXTURES / fixture).read_text()))


@respx.mock
async def test_the_list_call_filters_by_the_dotted_uid(shab_settings: Settings):
    # `uids=CHE000000004` silently returns nothing and `uid=` is silently
    # ignored; only the plural key with the dotted form filters
    route = respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(200, json=listing())
    )
    await persons(UID, settings=shab_settings)
    params = route.calls.last.request.url.params
    assert params.get_list("uids") == [DOTTED]
    assert params["publicationStates"] == "PUBLISHED"
    assert params["rubrics"] == "HR"


@respx.mock
async def test_every_publication_is_read_not_only_the_newest(
    shab_settings: Settings,
):
    respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(
            200, json=listing("1000000004", "1000000003", "1000000002")
        )
    )
    mock_xml("1000000004", "no_function.xml")
    mock_xml("1000000003", "no_block.xml")
    mock_xml("1000000002", "de_mutation.xml")

    found = await persons(UID, settings=shab_settings)

    assert [p.name for p in found] == [
        "Marco Keller",
        "Anna Beispiel",
        "Peter Muster",
        "Lena Probst",
    ]
    # the claim's age travels with it: SHAB names a past state
    assert found[0].published == "2025-09-01"
    assert found[1].published == "2025-03-14"
    assert found[1].source_url == (
        "https://shab.test/api/v1/publications/1000000002/xml"
    )


@respx.mock
async def test_no_publications_is_an_empty_answer(shab_settings: Settings):
    respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(200, json=listing())
    )
    assert await persons(UID, settings=shab_settings) == []


@respx.mock
async def test_a_notice_about_another_company_is_ignored(shab_settings: Settings):
    respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(200, json=listing("1000000005"))
    )
    mock_xml("1000000005", "fr_canton.xml")  # CHE-000.000.005, not ours
    assert await persons(UID, settings=shab_settings) == []


@respx.mock
async def test_an_implausible_total_means_the_filter_was_ignored(
    shab_settings: Settings,
):
    # the v0 pitfall: an ignored filter answers with the whole gazette
    respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(200, json=listing("x", total=110_764))
    )
    with pytest.raises(ShabError, match="filter"):
        await persons(UID, settings=shab_settings)


@respx.mock
async def test_a_failing_list_call_raises(shab_settings: Settings):
    respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(ShabError):
        await persons(UID, settings=shab_settings)


@respx.mock
async def test_a_failing_notice_raises(shab_settings: Settings):
    respx.get("https://shab.test/api/v1/publications").mock(
        return_value=httpx.Response(200, json=listing("1000000002"))
    )
    respx.get("https://shab.test/api/v1/publications/1000000002/xml").mock(
        side_effect=httpx.ConnectError("down")
    )
    with pytest.raises(ShabError):
        await persons(UID, settings=shab_settings)
