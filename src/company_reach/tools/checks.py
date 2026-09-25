"""Checking what a model said against the pages it said it from.

`extract` reads text a company's website wrote and answers with names and
e-mail addresses. A page can steer that answer — the audit's one P1
(`audit-2026-09-19.md:177`) — and a wrong address is the expensive case,
because a human is invited to write to it. So nothing the model returns is
believed on its own:

* a name has to appear in one of the pages, or the person is dropped;
* a name has to be somebody's: a firm — the company itself, a parent, an
  agency — or a single word is on the page for other reasons, and is
  dropped too;
* an address has to appear too, or it is dropped while the person stays,
  since being named is a separate claim from being reachable;
* an address on a domain other than the verified site's is kept and marked.
  Dropping it would hide from the reviewer that the page carries it at all,
  which is exactly what a planted contact wants.

Comparison runs through `textify.normalise`, the same fold the site quote
check uses, so a non-breaking space in an Impressum or a page written with ß
where the model typed ss cannot make a true name look invented.

Two fields are deliberately not checked. `description` and `size_signal` are
prose the model wrote rather than strings it copied, so there is nothing to
look for; and `addresses` are asked for as one line while a page writes them
across several, so a verbatim check would drop correct addresses for having
been tidied.
"""

import re

from company_reach.models import CompanyProfile, Person, RawProfile
from company_reach.nodes.find_site import strip_legal_form
from company_reach.tools.textify import normalise
from company_reach.tools.urls import email_domain, registered_domain

# Addresses that are furniture rather than contacts, measured in the probe
# (`research/website-reading.md:120-125`).
_NOISE_HOSTS = ("sentry", "wixpress", "example.", "schema.org", "w3.org")
_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_IMAGE = re.compile(r"\.(png|jpe?g|gif|svg|webp)$", re.IGNORECASE)
_RETINA = re.compile(r"@[23]x", re.IGNORECASE)
# Words a firm's name carries and a person's does not, compared as whole
# words, casefolded and without dots ("S.A." is "sa").
_FIRM_WORDS = {
    "ag", "gmbh", "sa", "sàrl", "sarl", "sagl", "holding", "kg", "ltd", "inc",
    "genossenschaft", "stiftung", "verein",
}  # fmt: skip


def is_noise(email: str) -> bool:
    """Not an address a person reads, whatever it looks like."""
    local, _, host = email.partition("@")
    return (
        bool(_IMAGE.search(email))
        or bool(_RETINA.search(email))
        or bool(_HEX32.match(local))
        or any(mark in host.lower() for mark in _NOISE_HOSTS)
    )


def appears_in(value: str, haystack: str) -> bool:
    """Whether the pages really carry this string, typography folded away."""
    return bool(value.strip()) and normalise(value) in haystack


def names_a_person(name: str, *, company: str, short_name_too: bool = False) -> bool:
    """Whether a name, from the site or from SHAB, is someone to greet.

    The audit found both failures behind "Guten Tag Muster Metallbau AG": the
    model listed the firm as a person, and presence passed because the firm's
    name is on every page of its site. A single word ("Kontakt", "Hans") is
    no better — it greets nobody in particular. So a name is refused when it
    is one word, carries a legal form ("Muster Holding AG" — a parent, an
    agency, the company itself), or is the register name.

    The firm's short name, legal form stripped, is refused only when the
    caller asks (`short_name_too`): a family firm carries its owner's name,
    and at "Hans Muster GmbH" the short name *is* the owner. `check_profile`
    asks when the model gave the name no role, since then nothing says it
    read a person; SHAB never asks, because its person blocks list people.
    """
    folded = normalise(name).strip(" ,.-")
    words = [word.strip(",()").replace(".", "") for word in folded.split()]
    if sum(any(c.isalpha() for c in word) for word in words) < 2:
        return False
    if _FIRM_WORDS.intersection(words):
        return False
    if folded == normalise(company).strip(" ,.-"):
        return False
    return not (short_name_too and folded == normalise(strip_legal_form(company)))


def checked(
    raw: RawProfile, *, texts: dict[str, str], site_url: str, company: str
) -> CompanyProfile:
    """The profile as it is allowed to leave M5."""
    haystack = normalise("\n".join(texts.values()))
    site = registered_domain(site_url)

    persons: list[Person] = []
    for person in raw.persons:
        if not appears_in(person.name, haystack):
            continue
        # without a role, nothing says the firm's short name was a person
        short_name_too = person.role is None
        if not names_a_person(
            person.name, company=company, short_name_too=short_name_too
        ):
            continue
        email = person.email
        if email is not None and (is_noise(email) or not appears_in(email, haystack)):
            email = None
        offsite = email is not None and email_domain(email) != site
        persons.append(
            Person(
                name=person.name,
                role=person.role,
                email=email,
                email_offsite=offsite,
            )
        )

    return CompanyProfile(
        description=raw.description,
        size_signal=raw.size_signal,
        persons=persons,
        addresses=raw.addresses,
        distributor_only=raw.distributor_only,
        foreign_group=raw.foreign_group,
    )
