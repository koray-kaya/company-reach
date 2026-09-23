"""The deterministic half of the untrusted-content boundary.

`extract` shows a model text written by a company's website and gets back
names and e-mail addresses. Everything here exists because that model can be
steered by the page it is reading (`audit-2026-09-19.md:177`), so nothing it
says is believed on its own: a name or an address has to be findable in the
page it was supposedly read from, and an address outside the verified site's
domain is kept but marked.

What is deliberately not checked, and why:

* `description` and `size_signal` are prose the model wrote, not strings it
  copied. There is nothing to look for.
* `addresses` are asked for as one line, while a page writes them across
  several. A verbatim check would drop correct addresses for being tidied.
"""

from company_reach.models import RawPerson, RawProfile
from company_reach.tools.checks import checked

SITE = "https://muster-metallbau.ch/"

IMPRESSUM = (
    "Impressum. Muster Metallbau AG, Beispielstrasse 1, 8000 Musterstadt. "
    "Geschäftsführerin Anna Muster, anna.muster@muster-metallbau.ch. "
    "Werkstatt: Beat Grossmann."
)


def raw(**over) -> RawProfile:
    fields = {
        "description": "Baut Metallteile.",
        "size_signal": None,
        "persons": [],
        "addresses": [],
        "distributor_only": False,
        "foreign_group": False,
    } | over
    return RawProfile(**fields)


def check(profile: RawProfile, text: str = IMPRESSUM):
    return checked(profile, texts={f"{SITE}impressum": text}, site_url=SITE)


# --- names -------------------------------------------------------------------


def test_a_name_on_the_page_is_kept():
    out = check(raw(persons=[RawPerson(name="Anna Muster", role="GL")]))
    assert [p.name for p in out.persons] == ["Anna Muster"]


def test_a_name_that_is_not_on_any_page_is_dropped():
    """The plainest form of the attack and of the ordinary failure: a name
    that reads perfectly and was never there."""
    out = check(raw(persons=[RawPerson(name="Klara Erfunden")]))
    assert out.persons == []


def test_a_name_differing_only_in_typography_is_kept():
    """A non-breaking space in the page, or ß where the model typed ss, must
    not make a true name look invented — the same normalisation the site
    quote check runs through (`audit-2026-09-19.md:178`)."""
    # Written as an escape on purpose: the whole point of this test is the
    # character between "Großmann" and "arbeitet", and a literal one is
    # invisible to whoever next tidies this file.
    page = "Geschäftsführer Hans Großmann\u00a0arbeitet hier."
    out = check(raw(persons=[RawPerson(name="Hans Grossmann")]), text=page)
    assert [p.name for p in out.persons] == ["Hans Grossmann"]


# --- e-mail addresses --------------------------------------------------------


def test_an_address_on_the_page_and_on_the_site_domain_is_clean():
    out = check(
        raw(
            persons=[
                RawPerson(name="Anna Muster", email="anna.muster@muster-metallbau.ch")
            ]
        )
    )
    assert out.persons[0].email == "anna.muster@muster-metallbau.ch"
    assert out.persons[0].email_offsite is False


def test_an_address_that_is_not_on_the_page_is_dropped_but_the_person_stays():
    """A guessed address is the expensive error: the invitation goes to it.
    The person was really named, so they are kept without one."""
    out = check(
        raw(persons=[RawPerson(name="Anna Muster", email="info@muster-metallbau.ch")])
    )
    assert out.persons[0].name == "Anna Muster"
    assert out.persons[0].email is None


def test_an_address_on_another_domain_is_marked_not_dropped():
    """The audit's acceptance case. It is on the page, so it is real; it is
    not on the site, so a human decides. Dropping it would hide the fact
    that the page carries it at all."""
    page = IMPRESSUM + " Kontakt: ceo@evil.example"
    out = check(
        raw(persons=[RawPerson(name="Anna Muster", email="ceo@evil.example")]), page
    )
    assert out.persons[0].email == "ceo@evil.example"
    assert out.persons[0].email_offsite is True


def test_www_does_not_make_the_site_a_different_domain():
    out = checked(
        raw(
            persons=[
                RawPerson(name="Anna Muster", email="anna.muster@muster-metallbau.ch")
            ]
        ),
        texts={"x": IMPRESSUM},
        site_url="https://www.muster-metallbau.ch/",
    )
    assert out.persons[0].email_offsite is False


# --- the noise the research measured -----------------------------------------


def test_a_wix_sentry_address_is_dropped():
    """Two of three Wix sites in the probe injected these, one with thirteen
    hits on a single page (`research/website-reading.md:120-123`)."""
    page = "Kontakt a1b2c3d4e5f67890a1b2c3d4e5f67890@sentry.wixpress.com"
    out = check(
        raw(
            persons=[
                RawPerson(
                    name="Anna Muster",
                    email="a1b2c3d4e5f67890a1b2c3d4e5f67890@sentry.wixpress.com",
                )
            ]
        ),
        text=IMPRESSUM + page,
    )
    assert out.persons[0].email is None


def test_a_retina_image_name_is_not_an_address():
    page = IMPRESSUM + " <img src=team@2x.png>"
    out = check(raw(persons=[RawPerson(name="Anna Muster", email="team@2x.png")]), page)
    assert out.persons[0].email is None


def test_a_schema_org_address_is_dropped():
    page = IMPRESSUM + " itemtype person@schema.org"
    out = check(
        raw(persons=[RawPerson(name="Anna Muster", email="person@schema.org")]), page
    )
    assert out.persons[0].email is None


# --- the rest of the profile -------------------------------------------------


def test_the_prose_and_the_flags_come_through_untouched():
    out = check(
        raw(
            description="Baut Treppen.",
            size_signal="seit 1974",
            addresses=["Beispielstrasse 1, 8000 Musterstadt"],
            distributor_only=True,
            foreign_group=True,
        )
    )
    assert out.description == "Baut Treppen."
    assert out.size_signal == "seit 1974"
    assert out.addresses == ["Beispielstrasse 1, 8000 Musterstadt"]
    assert out.distributor_only is True
    assert out.foreign_group is True


def test_a_name_may_be_found_on_any_of_the_pages():
    out = checked(
        raw(persons=[RawPerson(name="Beat Grossmann")]),
        texts={"a": "nothing here", "b": IMPRESSUM},
        site_url=SITE,
    )
    assert [p.name for p in out.persons] == ["Beat Grossmann"]
