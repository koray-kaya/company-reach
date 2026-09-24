"""Who the invitation goes to, decided by typed rules rather than a model.

The order is the research's. A named mail answers at 8-15%, `info@` at 2-6%
(`LEARNINGS.md` §5), so a name is worth real work; but a general inbox is
fine when it is all there is, as long as the greeting is not "Sehr geehrte
Damen und Herren".

1. Someone the site names, with their own address on the site's domain.
2. Someone the site names, without one: greeted by name at the site's
   general inbox — the one it publishes, or info@ constructed and marked as
   such when it publishes none (M6 open point 2).
3. Only when the site named nobody: the newest current person SHAB names,
   at the site's general inbox or a constructed info@, as in 2. SHAB is not
   asked at all otherwise (open point 3): it describes a past state, the
   site today.
4. Nobody named anywhere: the general inbox alone. Never a constructed one —
   an unnamed mail to a guessed inbox is the one that gets forwarded.

Among several people, one with their own address on the site comes first;
then the one who runs the company, then the board's chair, then everyone
else; a tie keeps the order of the page. Everyone not chosen is kept on the
contact as an alternative, so the reviewer can see who else was named.

An address on another domain is never promoted to an invitation. It is kept
as `third_party`, which `recommend` holds, because that is how a hostile page
plants a contact (`audit-2026-09-19.md:177`). The addresses on the pages are
read with a regex, not by the model: every one of them is a string the page
really carries.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from company_reach.models import CompanyProfile, Contact, Person, ShabPerson
from company_reach.nodes.find_site import SiteChoice
from company_reach.settings import Settings
from company_reach.tools import shab as shab_tool
from company_reach.tools.checks import appears_in, is_noise
from company_reach.tools.db import connect, record_contact
from company_reach.tools.textify import normalise
from company_reach.tools.urls import email_domain, registered_domain

Shab = Callable[..., Awaitable[list[ShabPerson]]]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# "info (at) muster.ch", "info[at]muster.ch", "muster [dot] ch"
_AT = re.compile(r"\s*[\(\[]\s*(?:at|ät)\s*[\)\]]\s*", re.IGNORECASE)
_DOT = re.compile(r"\s*[\(\[]\s*(?:dot|punkt)\s*[\)\]]\s*", re.IGNORECASE)

# Local parts of a general inbox rather than a person's.
_GENERIC = {
    "info", "kontakt", "contact", "office", "mail", "post", "hello", "hallo",
    "welcome", "admin", "sekretariat", "empfang", "verkauf", "sales",
    "service", "team", "buero", "bureau", "zentrale", "anfrage", "inquiry",
}  # fmt: skip

# Who runs the company, then who chairs its board. German, French and
# English, since the site writes roles in whatever language it is in.
_RANKS = (
    re.compile(
        r"geschäftsführ|geschäftsleit|inhaber|vorsitz|direktor|ceo|owner"
        r"|managing director|gérant|directeur|titulaire",
        re.IGNORECASE,
    ),
    re.compile(r"präsident|president|delegierte|administrat", re.IGNORECASE),
)


def rank(role: str | None) -> int:
    """0 runs the company, 1 chairs it, 2 any other role, 3 no role."""
    if not role:
        return len(_RANKS) + 1
    for i, pattern in enumerate(_RANKS):
        if pattern.search(role):
            return i
    return len(_RANKS)


def _own_address(person: Person) -> bool:
    return bool(person.email) and not person.email_offsite


def _describe(name: str, role: str | None, email: str | None = None) -> str:
    return ", ".join(part for part in (name, role, email) if part)


def page_addresses(texts: dict[str, str]) -> list[tuple[str, str]]:
    """Every address the pages carry, as (address, page url), first seen
    first. Noise — image names, tracking hosts — is left out."""
    seen: dict[str, str] = {}
    for url, text in texts.items():
        text = _DOT.sub(".", _AT.sub("@", text))
        for match in _EMAIL.finditer(text):
            email = match.group().strip(".").lower()
            if not is_noise(email) and email not in seen:
                seen[email] = url
    return list(seen.items())


def _page_with(value: str, texts: dict[str, str], fallback: str) -> str:
    for url, text in texts.items():
        if appears_in(value, normalise(text)):
            return url
    return fallback


def _inbox(
    addresses: list[tuple[str, str]], domain: str | None
) -> tuple[str, str] | None:
    for email, url in addresses:
        local = email.split("@", 1)[0]
        if email_domain(email) == domain and local in _GENERIC:
            return email, url
    return None


def _from_site(
    person: Person,
    *,
    texts: dict[str, str],
    addresses: list[tuple[str, str]],
    domain: str | None,
    site_url: str,
) -> Contact:
    found_at = _page_with(person.name, texts, site_url)
    base = {"name": person.name, "role": person.role, "source": "site"}
    if person.email and not person.email_offsite:
        url = _page_with(person.email, texts, found_at)
        return Contact(**base, email=person.email, email_kind="seen", source_url=url)
    if inbox := _inbox(addresses, domain):
        return Contact(
            **base, email=inbox[0], email_kind="generic", source_url=inbox[1]
        )
    if person.email:  # offsite: kept and marked, never an invitation
        return Contact(
            **base, email=person.email, email_kind="third_party", source_url=found_at
        )
    return Contact(
        **base, email=f"info@{domain}", email_kind="constructed", source_url=found_at
    )


def _without_site_names(
    shab_persons: list[ShabPerson],
    *,
    addresses: list[tuple[str, str]],
    domain: str | None,
) -> Contact | None:
    current = [p for p in shab_persons if not p.departed]
    # SHAB answers newest notice first; sorted() is stable, so within a rank
    # the newest claim stays in front
    ranked = sorted(current, key=lambda p: rank(p.role))
    chosen = ranked[0] if ranked else None
    inbox = _inbox(addresses, domain)

    if chosen is not None:
        # the same order as a name from the site: the published inbox, else
        # info@ guessed and marked (widened to SHAB names on 2026-09-24)
        if inbox:
            email, kind = inbox[0], "generic"
        else:
            email, kind = f"info@{domain}", "constructed"
        return Contact(
            name=chosen.name,
            role=chosen.role,
            email=email,
            email_kind=kind,
            source="shab",
            source_url=chosen.source_url,
            source_date=chosen.published,
            alternatives=[_describe(p.name, p.role) for p in ranked[1:]],
        )

    # nobody named: an inbox is sent to only when the site publishes one;
    # an address on another domain is kept, marked, and held
    if inbox:
        return Contact(
            email=inbox[0], email_kind="generic", source="site", source_url=inbox[1]
        )
    offsite = next(((e, u) for e, u in addresses if email_domain(e) != domain), None)
    if offsite:
        return Contact(
            email=offsite[0],
            email_kind="third_party",
            source="site",
            source_url=offsite[1],
        )
    return None


async def find_contact(
    state: dict[str, Any], *, settings: Settings, shab: Shab = shab_tool.persons
) -> dict:
    """`shab` is passed in for the reason `enrich_company` takes `child`: a
    test can hand it a double and see whether it was asked at all."""
    site: SiteChoice = state["site"]
    profile: CompanyProfile = state["profile"]
    texts: dict[str, str] = state.get("page_texts") or {}
    domain = registered_domain(site.url)
    addresses = page_addresses(texts)

    if profile.persons:
        ranked = sorted(
            profile.persons, key=lambda p: (not _own_address(p), rank(p.role))
        )
        contact = _from_site(
            ranked[0],
            texts=texts,
            addresses=addresses,
            domain=domain,
            site_url=site.url,
        )
        contact.alternatives = [_describe(p.name, p.role, p.email) for p in ranked[1:]]
    else:
        found = await shab(state["uid"], settings=settings)
        contact = _without_site_names(found, addresses=addresses, domain=domain)

    if contact is None:  # a finding: nobody named, no inbox published
        return {"contact": None, "contact_id": None}

    with connect(settings.db_path) as conn:
        contact_id = record_contact(conn, state["run_id"], state["uid"], contact)
    return {"contact": contact, "contact_id": contact_id}
