"""`find_contact`: who the invitation goes to, and why that address.

Typed rules, no model. The order is the design's (`design.md:137`) with the
two M6 decisions folded in: a named person with no address of their own is
greeted by name at `info@` (open point 2), and SHAB is asked only when the
site named nobody (open point 3).
"""

import sqlite3

import pytest

from company_reach.errors import ShabError
from company_reach.models import CompanyProfile, CompanyRecord, Person, ShabPerson
from company_reach.nodes.find_contact import find_contact
from company_reach.nodes.find_site import SiteChoice
from company_reach.tools.db import init_db

SITE = "https://muster-metallbau.ch/"
UID = "CHE000000046"
NOTICE = "https://shab.test/api/v1/publications/1/xml"
COMPANY = CompanyRecord(
    uid=UID,
    name="Muster Metallbau AG",
    legal_form="0106",
    municipality="3203",
    purpose="Metallbau.",
    purpose_head="Metallbau.",
)


class Shab:
    """Stands in for `tools.shab.persons` and remembers being asked."""

    def __init__(self, answer: list[ShabPerson] | Exception | None = None):
        self.answer = answer or []
        self.calls: list[str] = []

    async def __call__(self, uid: str, *, settings) -> list[ShabPerson]:
        self.calls.append(uid)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def shab_person(name: str, role: str | None, *, departed: bool = False) -> ShabPerson:
    return ShabPerson(
        name=name,
        role=role,
        departed=departed,
        published="2024-03-20",
        source_url=NOTICE,
    )


def state(persons: list[Person], pages: dict[str, str]) -> dict:
    return {
        "run_id": "run-1",
        "uid": UID,
        "company": COMPANY,
        "site": SiteChoice(SITE, "uid", "CHE-000.000.046", SITE),
        "profile": CompanyProfile(description="Baut Metallteile.", persons=persons),
        "page_texts": pages,
    }


@pytest.fixture
def db_settings(settings):
    init_db(settings.db_path)
    return settings


async def run(st: dict, settings, shab: Shab | None = None) -> dict:
    return await find_contact(st, settings=settings, shab=shab or Shab())


def contact_rows(settings) -> list[sqlite3.Row]:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("select * from contacts").fetchall()
    finally:
        conn.close()


# --- the site names someone --------------------------------------------------


async def test_a_personal_address_on_the_site_wins(db_settings):
    shab = Shab()
    page = f"{SITE}kontakt"
    out = await run(
        state(
            [
                Person(
                    name="Anna Muster",
                    role="Geschäftsführerin",
                    email="anna@muster-metallbau.ch",
                )
            ],
            {page: "Anna Muster, Geschäftsführerin, anna@muster-metallbau.ch"},
        ),
        db_settings,
        shab,
    )
    contact = out["contact"]
    assert (contact.name, contact.email, contact.email_kind, contact.source) == (
        "Anna Muster",
        "anna@muster-metallbau.ch",
        "seen",
        "site",
    )
    assert contact.source_url == page
    assert shab.calls == []  # open point 3: the site named someone


async def test_the_person_who_runs_the_company_is_chosen(db_settings):
    out = await run(
        state(
            [
                Person(
                    name="Beat Beispiel",
                    role="Mitglied des Verwaltungsrates",
                    email="beat@muster-metallbau.ch",
                ),
                Person(
                    name="Anna Muster",
                    role="Geschäftsführerin",
                    email="anna@muster-metallbau.ch",
                ),
            ],
            {
                SITE: "Beat Beispiel beat@muster-metallbau.ch "
                "Anna Muster anna@muster-metallbau.ch"
            },
        ),
        db_settings,
    )
    assert out["contact"].name == "Anna Muster"


async def test_an_equal_rank_keeps_the_order_of_the_page(db_settings):
    out = await run(
        state(
            [Person(name="Beat Beispiel"), Person(name="Anna Muster")],
            {SITE: "Beat Beispiel, Anna Muster"},
        ),
        db_settings,
    )
    assert out["contact"].name == "Beat Beispiel"


async def test_a_name_without_an_address_goes_to_the_general_inbox(db_settings):
    # obfuscated the way Swiss sites often write it
    out = await run(
        state(
            [Person(name="Anna Muster", role="Inhaberin")],
            {
                f"{SITE}impressum": "Anna Muster, Inhaberin. "
                "E-Mail: info (at) muster-metallbau.ch"
            },
        ),
        db_settings,
    )
    contact = out["contact"]
    assert (contact.name, contact.email, contact.email_kind) == (
        "Anna Muster",
        "info@muster-metallbau.ch",
        "generic",
    )


async def test_with_no_address_at_all_info_is_constructed_and_marked(db_settings):
    shab = Shab()
    out = await run(
        state(
            [Person(name="Anna Muster", role="Inhaberin")],
            {SITE: "Anna Muster, Inhaberin"},
        ),
        db_settings,
        shab,
    )
    contact = out["contact"]
    assert (contact.name, contact.email, contact.email_kind) == (
        "Anna Muster",
        "info@muster-metallbau.ch",
        "constructed",
    )
    assert shab.calls == []


async def test_an_offsite_address_is_third_party_not_an_invitation(db_settings):
    # check_profile kept the address and marked it; the mark is what holds it
    out = await run(
        state(
            [
                Person(
                    name="Anna Muster",
                    email="anna@elsewhere.example",
                    email_offsite=True,
                )
            ],
            {SITE: "Anna Muster anna@elsewhere.example"},
        ),
        db_settings,
    )
    contact = out["contact"]
    assert (contact.email, contact.email_kind) == (
        "anna@elsewhere.example",
        "third_party",
    )


async def test_a_web_designer_in_the_footer_does_not_displace_info(db_settings):
    out = await run(
        state(
            [Person(name="Anna Muster")],
            {SITE: "Anna Muster. Webdesign: studio@agentur.example"},
        ),
        db_settings,
    )
    assert out["contact"].email_kind == "constructed"


async def test_noise_that_looks_like_an_address_is_ignored(db_settings):
    out = await run(
        state([Person(name="Anna Muster")], {SITE: "Anna Muster logo@2x.png"}),
        db_settings,
    )
    assert out["contact"].email == "info@muster-metallbau.ch"


# --- the site names nobody ---------------------------------------------------


async def test_shab_names_the_person_when_the_site_does_not(db_settings):
    shab = Shab(
        [
            shab_person("Otto Alt", "Geschäftsführer", departed=True),
            shab_person("Beat Beispiel", "Mitglied des Verwaltungsrates"),
            shab_person("Anna Muster", "Gesellschafterin und Geschäftsführerin"),
        ]
    )
    out = await run(
        state([], {SITE: "Kontakt: info@muster-metallbau.ch"}), db_settings, shab
    )
    contact = out["contact"]
    assert shab.calls == [UID]
    assert (contact.name, contact.role, contact.source) == (
        "Anna Muster",
        "Gesellschafterin und Geschäftsführerin",
        "shab",
    )
    assert (contact.email, contact.email_kind) == (
        "info@muster-metallbau.ch",
        "generic",
    )
    # the claim's age is on the row: SHAB names a past state
    assert (contact.source_url, contact.source_date) == (NOTICE, "2024-03-20")


async def test_a_shab_name_alone_gets_no_constructed_address(db_settings):
    # open point 2 constructs only when the site itself named the person
    out = await run(
        state([], {SITE: "Willkommen"}),
        db_settings,
        Shab([shab_person("Anna Muster", "Geschäftsführerin")]),
    )
    contact = out["contact"]
    assert (contact.name, contact.email, contact.email_kind) == (
        "Anna Muster",
        None,
        None,
    )


async def test_only_departed_people_in_shab_is_nobody(db_settings):
    out = await run(
        state([], {SITE: "Kontakt: info@muster-metallbau.ch"}),
        db_settings,
        Shab([shab_person("Otto Alt", "Geschäftsführer", departed=True)]),
    )
    contact = out["contact"]
    assert (contact.name, contact.email, contact.email_kind) == (
        None,
        "info@muster-metallbau.ch",
        "generic",
    )


async def test_nothing_anywhere_is_a_finding(db_settings):
    out = await run(state([], {SITE: "Willkommen"}), db_settings)
    assert out["contact"] is None
    assert contact_rows(db_settings) == []


async def test_shab_failing_is_an_error_not_a_finding(db_settings):
    with pytest.raises(ShabError):
        await run(state([], {SITE: "Willkommen"}), db_settings, Shab(ShabError("503")))


# --- the record --------------------------------------------------------------


async def test_the_contact_is_recorded(db_settings):
    out = await run(
        state([Person(name="Anna Muster", role="Inhaberin")], {SITE: "Anna Muster"}),
        db_settings,
    )
    [row] = contact_rows(db_settings)
    assert (
        row["run_id"],
        row["uid"],
        row["name"],
        row["email_kind"],
        row["source"],
    ) == (
        "run-1",
        UID,
        "Anna Muster",
        "constructed",
        "site",
    )
    assert out["contact_id"] == row["id"]
