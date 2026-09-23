"""Checking what a model said against the pages it said it from.

`extract` reads text a company's website wrote and answers with names and
e-mail addresses. A page can steer that answer — the audit's one P1
(`audit-2026-09-19.md:177`) — and a wrong address is the expensive case,
because a human is invited to write to it. So nothing the model returns is
believed on its own:

* a name has to appear in one of the pages, or the person is dropped;
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
from company_reach.tools.textify import normalise
from company_reach.tools.urls import email_domain, registered_domain

# Addresses that are furniture rather than contacts, measured in the probe
# (`research/website-reading.md:120-125`).
_NOISE_HOSTS = ("sentry", "wixpress", "example.", "schema.org", "w3.org")
_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_IMAGE = re.compile(r"\.(png|jpe?g|gif|svg|webp)$", re.IGNORECASE)
_RETINA = re.compile(r"@[23]x", re.IGNORECASE)


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


def checked(raw: RawProfile, *, texts: dict[str, str], site_url: str) -> CompanyProfile:
    """The profile as it is allowed to leave M5."""
    haystack = normalise("\n".join(texts.values()))
    site = registered_domain(site_url)

    persons: list[Person] = []
    for person in raw.persons:
        if not appears_in(person.name, haystack):
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
