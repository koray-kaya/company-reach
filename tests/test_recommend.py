"""`recommend`: send, hold or skip, and always one reason the reviewer reads."""

import pytest

from company_reach.models import CompanyProfile, Contact
from company_reach.nodes.find_site import SiteChoice
from company_reach.nodes.recommend import recommend

SITE = SiteChoice("https://muster.ch/", "uid", "CHE-000.000.046", "https://muster.ch/")


def state(
    *,
    site: SiteChoice | None = SITE,
    distributor: bool = False,
    foreign: bool = False,
    contact: Contact | None = None,
) -> dict:
    return {
        "site": site,
        "profile": CompanyProfile(
            description="Baut Metallteile.",
            distributor_only=distributor,
            foreign_group=foreign,
        ),
        "contact": contact,
    }


def contact(email: str | None, kind: str | None, name: str | None = "Anna Muster"):
    return Contact(name=name, email=email, email_kind=kind, source="site")


CASES = [
    ("no site", state(site=None), "skip"),
    (
        "reseller",
        state(distributor=True, contact=contact("a@muster.ch", "seen")),
        "skip",
    ),
    (
        "foreign group",
        state(foreign=True, contact=contact("a@muster.ch", "seen")),
        "skip",
    ),
    ("nobody and nothing", state(contact=None), "hold"),
    ("a name, no address", state(contact=contact(None, None)), "hold"),
    ("another domain", state(contact=contact("a@x.example", "third_party")), "hold"),
    ("own address", state(contact=contact("a@muster.ch", "seen")), "send"),
    ("general inbox", state(contact=contact("info@muster.ch", "generic")), "send"),
    ("constructed", state(contact=contact("info@muster.ch", "constructed")), "send"),
    (
        "inbox, nobody named",
        state(contact=contact("info@muster.ch", "generic", name=None)),
        "send",
    ),
]


@pytest.mark.parametrize(("case", "st", "verdict"), CASES, ids=[c[0] for c in CASES])
def test_each_branch(case: str, st: dict, verdict: str):
    assert recommend(st)["recommendation"] == verdict


@pytest.mark.parametrize(("case", "st", "verdict"), CASES, ids=[c[0] for c in CASES])
def test_the_reason_is_never_empty(case: str, st: dict, verdict: str):
    # the reviewer reads it; "send" with no reason is a card nobody can check
    assert recommend(st)["reason"].strip()


def test_skip_comes_before_hold():
    # a reseller with no contact is not a company to chase for an address
    assert recommend(state(distributor=True))["recommendation"] == "skip"


def test_a_constructed_address_says_it_is_a_guess():
    out = recommend(state(contact=contact("info@muster.ch", "constructed")))
    assert "not seen" in out["reason"]


def test_a_lead_alone_is_held_and_says_so():
    lead = Contact(source="site", linkedin_lead="https://ch.linkedin.com/in/x")
    out = recommend(state(contact=lead))
    assert out["recommendation"] == "hold"
    assert "LinkedIn" in out["reason"] and "None" not in out["reason"]
