"""The node that puts `tools/checks` between the model and the database.

The checking itself is tested in `test_checks`. What matters here is the
wiring: the state field that downstream nodes read carries the checked
profile, and the table never sees the unchecked one.
"""

from company_reach.models import RawPerson, RawProfile
from company_reach.nodes.check_profile import check_profile
from company_reach.nodes.find_site import SiteChoice
from company_reach.tools.db import connect, init_db, profile_by_uid

SITE = "https://muster-metallbau.ch/"
UID = "CHE000000046"

IMPRESSUM = (
    "Impressum. Muster Metallbau AG, Beispielstrasse 1, 8000 Musterstadt. "
    "Anna Muster, anna.muster@muster-metallbau.ch"
)


def state(persons: list[RawPerson]) -> dict:
    return {
        "run_id": "run-1",
        "uid": UID,
        "site": SiteChoice(SITE, "uid", "CHE-000.000.046", SITE),
        "page_texts": {f"{SITE}impressum": IMPRESSUM},
        "raw_profile": RawProfile(
            description="Baut Metallteile.",
            size_signal=None,
            persons=persons,
            addresses=[],
            distributor_only=False,
            foreign_group=False,
        ),
    }


def test_the_checked_profile_is_what_downstream_reads(settings):
    init_db(settings.db_path)
    out = check_profile(
        state([RawPerson(name="Anna Muster"), RawPerson(name="Klara Erfunden")]),
        settings=settings,
    )
    assert [p.name for p in out["profile"].persons] == ["Anna Muster"]


def test_only_the_checked_profile_reaches_the_table(settings):
    """`extract` stores nothing, so this is the only writer. A row can never
    hold a name the pages do not carry, not even for the moments between two
    nodes of a run that then crashed."""
    init_db(settings.db_path)
    check_profile(
        state([RawPerson(name="Klara Erfunden", email="ceo@evil.example")]),
        settings=settings,
    )
    with connect(settings.db_path) as conn:
        stored = profile_by_uid(conn, "run-1", UID)
    assert stored is not None
    assert stored.persons == []


def test_a_company_whose_pages_name_nobody_is_still_recorded(settings):
    """An empty person list is a finding — the site really does name no one —
    and the row has to exist for M6 to tell that from never having looked."""
    init_db(settings.db_path)
    check_profile(state([]), settings=settings)
    with connect(settings.db_path) as conn:
        stored = profile_by_uid(conn, "run-1", UID)
    assert stored is not None and stored.persons == []


def test_a_planted_address_arrives_marked_rather_than_missing(settings):
    init_db(settings.db_path)
    texts = {f"{SITE}impressum": IMPRESSUM + " Kontakt: ceo@evil.example"}
    data = state([RawPerson(name="Anna Muster", email="ceo@evil.example")])
    data["page_texts"] = texts

    out = check_profile(data, settings=settings)

    person = out["profile"].persons[0]
    assert person.email == "ceo@evil.example"
    assert person.email_offsite is True
