"""`find_contact`: who the invitation goes to, and why that address.

Typed rules, no model. The order is the design's (`design.md:137`) with the
two M6 decisions folded in: a named person with no address of their own is
greeted by name at `info@` (open point 2), and SHAB is asked only when the
site named nobody (open point 3).
"""

import json
import sqlite3

import httpx
import pytest
import respx
from pydantic import SecretStr

from company_reach.errors import SearchError, ShabError
from company_reach.models import CompanyProfile, CompanyRecord, Person, ShabPerson
from company_reach.nodes.find_contact import find_contact
from company_reach.nodes.find_site import SiteChoice
from company_reach.tools.db import init_db
from company_reach.tools.search import Result

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


def shab_person(
    name: str,
    role: str | None,
    *,
    departed: bool = False,
    published: str = "2024-03-20",
    notice: str = NOTICE,
) -> ShabPerson:
    return ShabPerson(
        name=name,
        role=role,
        departed=departed,
        published=published,
        source_url=notice,
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


class Search:
    """Stands in for `tools.search.search` and remembers every query. No
    test here has an HTTP route to linkedin.com, and none needs one: the
    lead comes from a search result, never from a request to LinkedIn."""

    def __init__(self, results: list[Result] | None = None):
        self.results = results or []
        self.queries: list[str] = []

    async def __call__(self, query: str, *, settings, limit: int = 10) -> list[Result]:
        self.queries.append(query)
        return self.results


async def run(
    st: dict, settings, shab: Shab | None = None, search: Search | None = None
) -> dict:
    return await find_contact(
        st, settings=settings, shab=shab or Shab(), search=search or Search()
    )


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


@pytest.mark.parametrize(
    ("page", "expected", "origin"),
    [
        ("Ihre Ansprechpartnerin: Frau Anna Muster, Inhaber", "Frau", "page"),
        ("Fragen beantwortet Herr Dr. Muster gern.", "Herr", "page-surname"),
        ("Bitte wenden Sie sich an Herrn Muster.", "Herr", "page-surname"),
    ],
)
async def test_frau_before_the_name_sets_the_salutation(
    db_settings, page, expected, origin
):
    # the page itself says it: the one source a site gives that is not a
    # generic masculine role noun. Only the full name before it is certain.
    out = await run(
        state([Person(name="Anna Muster", role="Inhaber")], {SITE: page}),
        db_settings,
    )
    assert out["contact"].salutation == expected
    assert out["contact"].salutation_origin == origin
    [row] = contact_rows(db_settings)
    assert (row["salutation"], row["salutation_origin"]) == (expected, origin)


@pytest.mark.parametrize(
    ("page", "role"),
    [
        # the founder, her father: a surname alone may be anyone of that name
        (
            "1978 gründete Herr Muster die Firma. Heute führt Anna Muster den Betrieb.",
            None,
        ),
        # a couple: neither salutation is hers alone
        ("Herr und Frau Muster führen den Betrieb.", None),
        ("Ihre Ansprechpartner: Frau/Herr Muster", None),
        # a customer quote on the same page
        (
            "Anna Muster, Inhaberin. «Schnell und sauber» – Herr Muster, Kunde "
            "aus Bern",
            None,
        ),
        (
            "Anna Muster, Inhaberin. «Schnell und sauber» – Herr Muster, Kunde "
            "aus Bern",
            "Inhaberin",
        ),
    ],
)
async def test_a_salutation_that_may_be_someone_elses_sets_nothing(
    db_settings, page, role
):
    """Critical in review: "Herr Muster" elsewhere on the page gave Anna a
    Herr, unflagged. A wrong Frau/Herr is the worst mistake the mail can
    make; a missing one only falls back to the full name."""
    out = await run(
        state([Person(name="Anna Muster", role=role)], {SITE: page}), db_settings
    )
    assert out["contact"].salutation is None


async def test_a_lone_first_name_is_nobody_named(db_settings):
    """Review: "Reto, Inhaber" got a guessed info@ and a Send, although the
    mail treats a lone first name as nobody named — and nobody named is
    never written to at a guessed address."""
    shab = Shab()
    out = await run(
        state([Person(name="Reto", role="Inhaber")], {SITE: "Reto, Inhaber"}),
        db_settings,
        shab,
    )
    assert out["contact"] is None  # no inbox published: a hold
    assert shab.calls == [UID]  # asked, as when the site names nobody


async def test_a_lone_first_name_with_an_inbox_writes_to_the_inbox(db_settings):
    out = await run(
        state(
            [Person(name="Reto", role="Inhaber")],
            {SITE: "Reto, Inhaber. info@muster-metallbau.ch"},
        ),
        db_settings,
    )
    contact = out["contact"]
    assert (contact.name, contact.email_kind) == (None, "generic")


async def test_a_named_colleague_is_chosen_over_a_lone_first_name(db_settings):
    out = await run(
        state(
            [
                Person(name="Reto", role="Inhaber"),
                Person(name="Anna Muster", role="Leiterin Verkauf"),
            ],
            {SITE: "Reto, Inhaber. Anna Muster, Leiterin Verkauf"},
        ),
        db_settings,
    )
    assert out["contact"].name == "Anna Muster"
    assert all(not a.startswith("Reto") for a in out["contact"].alternatives)


async def test_a_page_salutation_against_a_feminine_role_sets_nothing(db_settings):
    out = await run(
        state(
            [Person(name="Anna Muster", role="Inhaberin")],
            {SITE: "Ihr Ansprechpartner: Herr Muster"},
        ),
        db_settings,
    )
    assert out["contact"].salutation is None


async def test_a_bare_role_sets_nothing(db_settings):
    # "Inhaber" on a site says nothing about who holds it
    out = await run(
        state(
            [Person(name="Anna Muster", role="Inhaber")],
            {SITE: "Anna Muster, Inhaber. Herr Beispiel, Werkstatt."},
        ),
        db_settings,
    )
    assert out["contact"].salutation is None


async def test_two_salutations_for_one_name_set_nothing(db_settings):
    out = await run(
        state(
            [Person(name="Anna Muster", role="Inhaber")],
            {SITE: "Frau Muster (Büro), Herr Muster (Werkstatt)"},
        ),
        db_settings,
    )
    assert out["contact"].salutation is None


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


async def test_a_shab_name_alone_is_greeted_at_a_constructed_info(db_settings):
    # widened on 2026-09-24: 3 of 11 live companies had a SHAB name and no
    # address at all, and a guessed info@ costs at most a bounce
    out = await run(
        state([], {SITE: "Willkommen"}),
        db_settings,
        Shab([shab_person("Anna Muster", "Geschäftsführerin")]),
    )
    contact = out["contact"]
    assert (contact.name, contact.email, contact.email_kind, contact.source) == (
        "Anna Muster",
        "info@muster-metallbau.ch",
        "constructed",
        "shab",
    )


async def test_a_shab_name_prefers_info_over_an_offsite_address(db_settings):
    # the same order as a name from the site: a footer credit on another
    # domain does not displace the company's own inbox
    out = await run(
        state([], {SITE: "Webdesign: studio@agentur.example"}),
        db_settings,
        Shab([shab_person("Anna Muster", "Geschäftsführerin")]),
    )
    assert out["contact"].email_kind == "constructed"


async def test_nobody_named_is_never_given_a_constructed_address(db_settings):
    # a guessed inbox with no name to greet is the "Damen und Herren" mail
    # the research says gets forwarded rather than answered
    out = await run(state([], {SITE: "Webdesign: studio@agentur.example"}), db_settings)
    contact = out["contact"]
    assert (contact.name, contact.email_kind) == (None, "third_party")


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


# --- one person, many notices (audit K2) ------------------------------------
# Every notice names people as they stood on its date, so one person turns up
# once per notice. The newest notice that mentions someone decides: struck out
# there, they are gone, whatever an older notice said.

OLD_NOTICE = "https://shab.test/api/v1/publications/0/xml"


async def test_a_person_struck_out_later_is_not_chosen(db_settings):
    """The audit's probe: entered in 2012, struck out in 2024. The older
    entry alone reads as current, and it was chosen over the current board
    member because its role ranks higher."""
    shab = Shab(
        [
            shab_person(
                "Nina Neu", "Mitglied des Verwaltungsrates", published="2024-05-01"
            ),
            shab_person(
                "Otto Alt", "Geschäftsführer", departed=True, published="2024-05-01"
            ),
            shab_person(
                "Otto Alt", "Geschäftsführer", published="2012-03-01", notice=OLD_NOTICE
            ),
        ]
    )
    out = await run(
        state([], {SITE: "Kontakt: info@muster-metallbau.ch"}), db_settings, shab
    )
    contact = out["contact"]
    assert contact.name == "Nina Neu"
    assert contact.alternatives == []  # nor is he offered as someone else


async def test_the_newest_notice_decides_whatever_order_shab_answers_in(db_settings):
    # SHAB happens to answer newest first today, but nothing asks it to
    shab = Shab(
        [
            shab_person(
                "Otto Alt", "Geschäftsführer", published="2012-03-01", notice=OLD_NOTICE
            ),
            shab_person(
                "Otto Alt", "Geschäftsführer", departed=True, published="2024-05-01"
            ),
            shab_person(
                "Nina Neu", "Mitglied des Verwaltungsrates", published="2024-05-01"
            ),
        ]
    )
    out = await run(state([], {SITE: "Willkommen"}), db_settings, shab)
    assert out["contact"].name == "Nina Neu"
    assert out["contact"].alternatives == []


async def test_a_changed_role_is_the_newest_and_the_person_is_named_once(db_settings):
    """A former managing director who now only sits on the board: the old
    role was put in the invitation, and the card listed them as their own
    alternative."""
    shab = Shab(
        [
            shab_person(
                "Peter Muster", "Mitglied des Verwaltungsrates", published="2025-01-10"
            ),
            shab_person(
                "Peter Muster",
                "Geschäftsführer",
                published="2019-03-01",
                notice=OLD_NOTICE,
            ),
        ]
    )
    out = await run(state([], {SITE: "Willkommen"}), db_settings, shab)
    contact = out["contact"]
    assert (contact.name, contact.role) == (
        "Peter Muster",
        "Mitglied des Verwaltungsrates",
    )
    assert (contact.source_url, contact.source_date) == (NOTICE, "2025-01-10")
    assert contact.alternatives == []


async def test_a_person_named_again_after_leaving_is_current(db_settings):
    shab = Shab(
        [
            shab_person("Anna Muster", "Geschäftsführerin", published="2025-02-01"),
            shab_person(
                "Anna Muster",
                "Geschäftsführerin",
                departed=True,
                published="2020-06-01",
                notice=OLD_NOTICE,
            ),
        ]
    )
    out = await run(state([], {SITE: "Willkommen"}), db_settings, shab)
    assert out["contact"].name == "Anna Muster"


async def test_the_same_name_written_differently_is_one_person(db_settings):
    shab = Shab(
        [
            shab_person(
                "Otto  ALT", "Geschäftsführer", departed=True, published="2024-05-01"
            ),
            shab_person(
                "Otto Alt", "Geschäftsführer", published="2012-03-01", notice=OLD_NOTICE
            ),
        ]
    )
    out = await run(
        state([], {SITE: "Kontakt: info@muster-metallbau.ch"}), db_settings, shab
    )
    assert out["contact"].name is None


async def test_a_firm_or_a_single_word_in_shab_is_not_a_person(db_settings):
    """The same rule as for a name read off the site: a register entry that
    is the firm itself, or one word, is no one to greet."""
    shab = Shab(
        [
            shab_person("Muster Metallbau AG", "Gesellschafterin"),
            shab_person("Muster", "Geschäftsführer"),
            shab_person("Anna Muster", "Mitglied des Verwaltungsrates"),
        ]
    )
    out = await run(state([], {SITE: "Willkommen"}), db_settings, shab)
    assert out["contact"].name == "Anna Muster"
    assert out["contact"].alternatives == []


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


# --- everyone else who was named ---------------------------------------------
# One person gets the invitation, but the reviewer sees everyone the sources
# named, so a wrong choice can be corrected by hand rather than lost.


async def test_the_other_people_on_the_site_are_kept_in_rank_order(db_settings):
    out = await run(
        state(
            [
                Person(name="Beat Beispiel", role="Mitglied des Verwaltungsrates"),
                Person(name="Anna Muster", role="Inhaberin"),
                Person(
                    name="Carla Probst", role="Inhaberin", email="c@muster-metallbau.ch"
                ),
            ],
            {SITE: "Beat Beispiel, Anna Muster, Carla Probst c@muster-metallbau.ch"},
        ),
        db_settings,
    )
    assert out["contact"].name == "Carla Probst"  # her own address wins
    assert out["contact"].alternatives == [
        "Anna Muster, Inhaberin",
        "Beat Beispiel, Mitglied des Verwaltungsrates",
    ]


async def test_the_other_current_people_in_shab_are_kept(db_settings):
    shab = Shab(
        [
            shab_person("Otto Alt", "Geschäftsführer", departed=True),
            shab_person("Beat Beispiel", None),
            shab_person("Anna Muster", "Geschäftsführerin"),
        ]
    )
    out = await run(state([], {SITE: "Willkommen"}), db_settings, shab)
    assert out["contact"].name == "Anna Muster"
    assert out["contact"].alternatives == ["Beat Beispiel"]


async def test_the_alternatives_are_recorded(db_settings):
    await run(
        state(
            [
                Person(name="Anna Muster", role="Inhaberin"),
                Person(name="Beat Beispiel"),
            ],
            {SITE: "Anna Muster, Beat Beispiel"},
        ),
        db_settings,
    )
    [row] = contact_rows(db_settings)
    assert json.loads(row["alternatives"]) == ["Beat Beispiel"]


# --- the LinkedIn lead -------------------------------------------------------
# Looked for only when the tool has no address it saw: a constructed info@,
# or nothing at all. A lead is a profile URL for a human to check, never an
# address to write to, and LinkedIn itself is never requested.

ANNA_IN = Result(
    "https://ch.linkedin.com/in/anna-muster-123",
    "Anna Muster - Inhaberin - Muster Metallbau AG | LinkedIn",
    "Musterstadt",
    "ddg",
)


async def test_a_constructed_address_looks_for_a_lead(db_settings):
    search = Search([ANNA_IN])
    out = await run(
        state([Person(name="Anna Muster", role="Inhaberin")], {SITE: "Anna Muster"}),
        db_settings,
        search=search,
    )
    [query] = search.queries
    assert query.startswith("site:linkedin.com/in ")
    assert '"Anna Muster"' in query and '"Muster Metallbau"' in query
    contact = out["contact"]
    assert contact.linkedin_lead == ANNA_IN.url
    assert contact.email == "info@muster-metallbau.ch"  # never the lead


async def test_a_published_address_needs_no_lead(db_settings):
    search = Search([ANNA_IN])
    await run(
        state(
            [Person(name="Anna Muster")], {SITE: "Anna Muster info@muster-metallbau.ch"}
        ),
        db_settings,
        search=search,
    )
    assert search.queries == []


async def test_a_namesake_elsewhere_is_not_a_lead(db_settings):
    # a name alone gives false matches (LEARNINGS §5): the result has to
    # carry the company too
    other = Result(
        "https://ch.linkedin.com/in/anna-muster-9",
        "Anna Muster - Pflegefachfrau - Spital Beispiel | LinkedIn",
        "Bern",
        "ddg",
    )
    out = await run(
        state([Person(name="Anna Muster")], {SITE: "Anna Muster"}),
        db_settings,
        search=Search([other]),
    )
    assert out["contact"].linkedin_lead is None


async def test_only_a_profile_page_is_a_lead(db_settings):
    company_page = Result(
        "https://ch.linkedin.com/company/muster-metallbau",
        "Anna Muster at Muster Metallbau AG | LinkedIn",
        "",
        "ddg",
    )
    out = await run(
        state([Person(name="Anna Muster")], {SITE: "Anna Muster"}),
        db_settings,
        search=Search([company_page]),
    )
    assert out["contact"].linkedin_lead is None


async def test_with_nobody_found_the_company_is_looked_up(db_settings):
    search = Search([ANNA_IN])
    out = await run(state([], {SITE: "Willkommen"}), db_settings, search=search)
    [query] = search.queries
    assert query == 'site:linkedin.com/in "Muster Metallbau"'
    contact = out["contact"]
    assert (contact.name, contact.email, contact.linkedin_lead) == (
        None,
        None,
        ANNA_IN.url,
    )


@respx.mock
async def test_the_lead_search_never_asks_brave(db_settings):
    """The lead is a URL we store, and the query carries a person's name.
    Brave's terms forbid storing its results, so the lead search is
    SearXNG's alone, key or no key: when SearXNG fails, it fails."""
    keyed = db_settings.model_copy(
        update={"brave_search_api_key": SecretStr("brave-test-key"), "search_gap_s": 0}
    )
    respx.get("http://searxng:8080/search").mock(return_value=httpx.Response(503))
    brave = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={"web": {"results": [{"url": ANNA_IN.url, "title": ANNA_IN.title}]}},
        )
    )
    with pytest.raises(SearchError):
        await find_contact(
            state([Person(name="Anna Muster")], {SITE: "Anna Muster"}),
            settings=keyed,
            shab=Shab(),
        )
    assert not brave.called


async def test_the_lead_is_recorded(db_settings):
    await run(
        state([Person(name="Anna Muster")], {SITE: "Anna Muster"}),
        db_settings,
        search=Search([ANNA_IN]),
    )
    [row] = contact_rows(db_settings)
    assert row["linkedin_lead"] == ANNA_IN.url


# --- every address considered (M7 open point 3) ------------------------------
# The review card offers each as a row, the chosen one pre-selected.


def pairs(contact) -> list[tuple[str, str]]:
    return [(a.email, a.kind) for a in contact.addresses]


async def test_the_chosen_address_comes_first_and_the_others_follow(db_settings):
    out = await run(
        state(
            [Person(name="Anna Muster", role="Inhaberin")],
            {
                SITE: "Anna Muster. info@muster-metallbau.ch · "
                "verkauf@muster-metallbau.ch · beat.beispiel@muster-metallbau.ch · "
                "Webdesign: studio@agentur.example"
            },
        ),
        db_settings,
    )
    assert pairs(out["contact"]) == [
        ("info@muster-metallbau.ch", "generic"),
        ("verkauf@muster-metallbau.ch", "generic"),
        ("beat.beispiel@muster-metallbau.ch", "seen"),
        ("studio@agentur.example", "third_party"),
    ]


async def test_a_constructed_address_is_offered_as_constructed(db_settings):
    out = await run(
        state([Person(name="Anna Muster")], {SITE: "Anna Muster"}), db_settings
    )
    assert pairs(out["contact"]) == [("info@muster-metallbau.ch", "constructed")]


async def test_the_lead_is_never_an_address(db_settings):
    out = await run(
        state([Person(name="Anna Muster")], {SITE: "Anna Muster"}),
        db_settings,
        search=Search([ANNA_IN]),
    )
    assert out["contact"].linkedin_lead == ANNA_IN.url
    assert all("linkedin" not in a.email for a in out["contact"].addresses)


async def test_the_addresses_are_recorded(db_settings):
    await run(
        state(
            [Person(name="Anna Muster")], {SITE: "Anna Muster info@muster-metallbau.ch"}
        ),
        db_settings,
    )
    [row] = contact_rows(db_settings)
    assert json.loads(row["addresses"]) == [
        {"email": "info@muster-metallbau.ch", "kind": "generic"}
    ]


# --- the site's own name under another ending (#25) -------------------------
# Decided with Koray (M8 open point 4): an address read from the site's own
# pages whose domain is the site's name under another TLD counts as on-site;
# a longer name, a subdomain or a model-given person's address does not.


async def test_the_sites_name_under_another_ending_is_its_own_inbox(db_settings):
    out = await run(
        state([], {SITE: "Kontakt: info@muster-metallbau.com"}), db_settings
    )
    contact = out["contact"]
    assert (contact.email, contact.email_kind) == (
        "info@muster-metallbau.com",
        "generic",
    )
    [row] = contact.addresses
    assert row.kind == "generic" and "another ending" in row.note


async def test_a_longer_name_under_another_ending_is_still_third_party(db_settings):
    out = await run(
        state([], {SITE: "Kontakt: info@muster-metallbau-shop.com"}), db_settings
    )
    assert out["contact"].email_kind == "third_party"


async def test_a_subdomain_under_another_ending_is_still_third_party(db_settings):
    out = await run(
        state([], {SITE: "Kontakt: info@mail.muster-metallbau.com"}), db_settings
    )
    assert out["contact"].email_kind == "third_party"


async def test_a_persons_address_under_another_ending_keeps_the_strict_rule(
    db_settings,
):
    # check_profile marked it off-site; the model's persons are not relaxed
    out = await run(
        state(
            [
                Person(
                    name="Anna Muster",
                    email="anna@muster-metallbau.com",
                    email_offsite=True,
                )
            ],
            {SITE: "Anna Muster anna@muster-metallbau.com"},
        ),
        db_settings,
    )
    assert out["contact"].email_kind == "third_party"
