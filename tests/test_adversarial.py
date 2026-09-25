"""What a hostile page gets, against the half of the defence that cannot be
talked out of it.

The audit's P1 acceptance has two halves (`audit-2026-09-19.md:186`). One is
about the model — does it obey an injected instruction — and belongs to the
opt-in golden evaluation, because it needs the endpoint and a number of
trials. The other is this: whatever the model can be talked into returning,
`check_profile` decides what survives, and that decision runs with no model
at all.

So each test here hands `checked` the *worst* answer the page could have
produced — as if the model had fully complied — and asserts what is left.
"""

import re
from pathlib import Path

from company_reach.models import RawPerson, RawProfile
from company_reach.tools.checks import checked
from company_reach.tools.textify import textify
from company_reach.tools.untrusted import as_data

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
SITE = "https://beispiel-holzbau.ch/"


def page(name: str) -> str:
    return textify((FIXTURES / f"{name}.html").read_text(encoding="utf-8"))


def raw(persons: list[RawPerson], **over) -> RawProfile:
    fields = {
        "description": "Baut Treppen.",
        "size_signal": None,
        "persons": persons,
        "addresses": [],
        "distributor_only": False,
        "foreign_group": False,
    } | over
    return RawProfile(**fields)


def check(profile: RawProfile, name: str):
    return checked(
        profile,
        texts={f"{SITE}impressum": page(name)},
        site_url=SITE,
        company="Beispiel Holzbau GmbH",
    )


def test_the_planted_person_and_address_do_not_both_survive():
    """The page names a person who does not work there and an address on
    somebody else's domain. The name is on the page, so it is kept — the
    check proves presence, not employment — but the address is marked, and
    marked is what M6 turns into `hold`."""
    out = check(
        raw([RawPerson(name="Dr. Konrad Weiss", email="ceo@evil.example")]),
        "poisoned_instructions",
    )
    assert out.persons[0].email == "ceo@evil.example"
    assert out.persons[0].email_offsite is True


def test_an_injection_cannot_add_a_person_who_is_not_written_down():
    """A page that talks to the assistant can make it invent a colleague as
    easily as a contact; that half is caught by presence alone."""
    out = check(
        raw([RawPerson(name="Hans Niemand", email="hans@evil.example")]),
        "poisoned_instructions",
    )
    assert out.persons == []


def test_the_page_cannot_end_the_block_it_sits_in():
    """The same page also writes the closing marker, so the two halves of
    the boundary are exercised on one fixture."""
    block = as_data(page("poisoned_instructions"), url=f"{SITE}impressum")
    [end] = re.findall(r"<<<END[^>]*>>>", block)
    assert block.endswith(end)


def test_a_lookalike_domain_is_marked_not_waved_through():
    """`beispiel-holzbau.support` reads like the company and is not the
    company. Nothing about it is malformed, which is the point: only the
    comparison with the verified site catches it."""
    out = check(
        raw(
            [
                RawPerson(
                    name="Renate Beispiel",
                    email="renate.beispiel@beispiel-holzbau.support",
                )
            ]
        ),
        "lookalike_contact",
    )
    assert out.persons[0].email_offsite is True


def test_a_group_page_names_no_person_for_its_parent():
    """A parent's Impressum on a subsidiary's site. The parent is a firm by
    its legal form, so it is no person to greet; its address is still on
    the page, and `find_contact` offers it as third party, which holds."""
    out = check(
        raw(
            [
                RawPerson(
                    name="Beispiel Gruppe Holding AG", email="info@beispiel-gruppe.ch"
                )
            ]
        ),
        "group_with_other_uid",
    )
    assert out.persons == []


def test_a_directory_page_yields_no_contact_for_the_company():
    """Directories publish UIDs and addresses of many firms. Nothing on this
    page is an address for our company, and nothing survives — not even
    the "person", who is the firm itself."""
    out = check(
        raw(
            [RawPerson(name="Beispiel Holzbau GmbH", email="info@beispiel-holzbau.ch")]
        ),
        "directory_entry",
    )
    assert out.persons == []


def test_what_the_check_does_not_catch_is_written_down_too():
    """The residual risk, on purpose and in a test so it cannot be forgotten.

    Presence is not employment. A page that writes "the contact is Dr. Konrad
    Weiss, info@beispiel-holzbau.ch" gets both halves past this node: the name
    is on the page, and the address is on the verified site, so nothing is
    marked. What is left is a real company address with a wrong name on it —
    the invitation reaches the company, greeting a person who does not work
    there.

    Nothing deterministic closes that: it needs a source of truth about who
    works where, which is what SHAB gives M6 (`design.md:79`). The reviewer
    reading the card is the control until then, and this test exists so that
    the next person to read `checks.py` learns the limit from the tests
    rather than from a bad draft.
    """
    out = check(
        raw([RawPerson(name="Dr. Konrad Weiss", email="info@beispiel-holzbau.ch")]),
        "poisoned_instructions",
    )
    assert out.persons[0].name == "Dr. Konrad Weiss"
    assert out.persons[0].email == "info@beispiel-holzbau.ch"
    assert out.persons[0].email_offsite is False
