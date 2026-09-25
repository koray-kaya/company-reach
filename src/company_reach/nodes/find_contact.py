"""Who the invitation goes to, decided by typed rules rather than a model.

The order is the research's. A named mail answers at 8-15%, `info@` at 2-6%
(`LEARNINGS.md` §5), so a name is worth real work; but a general inbox is
fine when it is all there is, as long as the greeting is not "Sehr geehrte
Damen und Herren".

1. Someone the site names, with their own address on the site's domain.
2. Someone the site names, without one: greeted by name at the site's
   general inbox — the one it publishes, or info@ constructed and marked as
   such when it publishes none (M6 open point 2). Only on a domain of the
   site's own: on a subdomain or a site builder's host there is no inbox to
   guess, and the person is kept without an address (`urls.inbox_domain`).
3. Only when the site named nobody: the newest current person SHAB names,
   at the site's general inbox or a constructed info@, as in 2. SHAB is not
   asked at all otherwise (open point 3): it describes a past state, the
   site today. A person counts once, as the newest notice naming them has
   it, and not at all when that notice struck them out (`shab.current`).
4. Nobody named anywhere: the general inbox alone. Never a constructed one —
   an unnamed mail to a guessed inbox is the one that gets forwarded.

Among several people, one with their own address on the site comes first;
then the one who runs the company, then the board's chair, then everyone
else; a tie keeps the order of the page. Everyone not chosen is kept on the
contact as an alternative, so the reviewer can see who else was named.

When the tool saw no address — it constructed info@, could not, or found
nothing at all — one web search restricted to LinkedIn profiles looks for a
lead: the person's name and the company, or the company alone. It goes to
SearXNG only, never to Brave: the lead is stored, and Brave's results may not
be. A result counts only if its title carries the company (and the surname,
when there is one), because a name alone matches namesakes elsewhere. The
lead is a URL for a human to check; it is never an address and LinkedIn
itself is never asked.

An address on another domain is never promoted to an invitation. It is kept
as `third_party`, which `recommend` holds, because that is how a hostile page
plants a contact (`audit-2026-09-19.md:177`). The addresses on the pages are
read with a regex, not by the model: every one of them is a string the page
really carries.

A salutation is set only when a page writes it before the person's surname
("Frau Muster", "Herrn Dr. Muster"). A role noun on a site says nothing —
sites write "Inhaber" for a woman too — and a SHAB role travels unchanged,
so `tools/invitation.salutation` can read its gender itself.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from company_reach.models import (
    CompanyProfile,
    Contact,
    ContactAddress,
    Person,
    ShabPerson,
)
from company_reach.nodes.find_site import SiteChoice, strip_legal_form
from company_reach.settings import Settings
from company_reach.tools import search as search_tool
from company_reach.tools import shab as shab_tool
from company_reach.tools.checks import appears_in, is_noise, names_a_person
from company_reach.tools.db import connect, record_contact
from company_reach.tools.invitation import (
    can_be_addressed,
    is_feminine_role,
    split_name,
)
from company_reach.tools.search import Result
from company_reach.tools.textify import normalise
from company_reach.tools.urls import email_domain, inbox_domain, registered_domain

Shab = Callable[..., Awaitable[list[ShabPerson]]]
Search = Callable[..., Awaitable[list[Result]]]

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


def other_ending(email_domain_: str, site: str | None) -> bool:
    """`muster.com` for the site `muster.ch`: the same name under another
    TLD, and nothing more — no subdomain, no longer name (#25, decided with
    Koray). Only ever asked of an address read from the site's own pages."""
    if not site:
        return False
    a, b = email_domain_.split("."), site.split(".")
    return len(a) == len(b) == 2 and a[0] == b[0] and a[1] != b[1]


def _on_site(email: str, site: str | None) -> bool:
    # the same registered domain (`mail.muster.ch` is `muster.ch`), or the
    # site's own name under another ending — asked of the address's host as
    # written, since #25 admits no subdomain there
    host = email.rsplit("@", 1)[-1].lower().removeprefix("www.")
    return email_domain(email) == site or other_ending(host, site)


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
        if _on_site(email, domain) and local in _GENERIC:
            return email, url
    return None


# "Herr und Frau Muster", "Frau/Herr Muster", "Herr & Frau Muster": a couple
# or a form of address, never one person's salutation.
_JOINED = re.compile(r"\b(?:Frau|Herrn?)\s*(?:und|&|/)\s*$")
_SALUTATION_WORDS = {"Frau", "Herr", "Herrn"}


def _surname_after_a_given_name(surname: str, texts: dict[str, str]) -> bool:
    """Whether the pages write this surname after a capitalised word that
    is not a salutation — someone's given name, the person's own included,
    or a word like "Firma" or "Familie"."""
    before = re.compile(rf"\b([A-ZÄÖÜ][\w-]*)\s+{re.escape(surname)}\b")
    return any(
        m.group(1) not in _SALUTATION_WORDS
        for text in texts.values()
        for m in before.finditer(text)
    )


def stated_salutation(
    name: str, texts: dict[str, str], role: str | None = None
) -> tuple[str, str] | None:
    """("Frau" | "Herr", origin) when the pages write it before this person,
    else None. Only a match that cannot be someone else's counts:

    * "Frau Anna Muster", before the full name, is origin "page";
    * "Frau Muster", before the surname alone, counts ("page-surname") only
      if the pages never write the surname after a given name — "1978
      gründete Herr Muster die Firma. Heute führt Anna Muster ..." names a
      father and a daughter, and the surname alone cannot tell which;
    * a salutation joined to another ("Herr und Frau Muster") is ignored;
    * both salutations, or a page's Herr against a feminine role, give
      nothing.

    Nothing is the safe answer: the greeting falls back to the full name
    and the card asks the reviewer."""
    parts = split_name(name)
    if not parts.surname:
        return None
    given = r"\s+".join(re.escape(g) for g in parts.given)
    pattern = re.compile(
        rf"\b(Frau|Herrn?)\s+(?:(?:Prof|Dr)\.\s*)*(?:({given})\s+)?"
        rf"{re.escape(parts.surname)}\b"
    )
    full: set[str] = set()
    bare: set[str] = set()
    for text in texts.values():
        for match in pattern.finditer(text):
            if _JOINED.search(text[: match.start()]):
                continue
            word = "Frau" if match.group(1) == "Frau" else "Herr"
            (full if match.group(2) else bare).add(word)
    if full:
        said, origin = full, "page"
    elif bare and not _surname_after_a_given_name(parts.surname, texts):
        said, origin = bare, "page-surname"
    else:
        return None
    if len(said) != 1:
        return None
    word = said.pop()
    if word == "Herr" and is_feminine_role(role):
        return None
    return word, origin


def _from_site(
    person: Person,
    *,
    texts: dict[str, str],
    addresses: list[tuple[str, str]],
    domain: str | None,
    guess: str | None,
    site_url: str,
) -> Contact:
    found_at = _page_with(person.name, texts, site_url)
    stated = stated_salutation(person.name, texts, person.role)
    base = {
        "name": person.name,
        "role": person.role,
        "source": "site",
        "salutation": stated[0] if stated else None,
        "salutation_origin": stated[1] if stated else None,
    }
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
    if guess is None:  # no domain of the site's own to guess an inbox on
        return Contact(**base, source_url=found_at)
    return Contact(
        **base, email=f"info@{guess}", email_kind="constructed", source_url=found_at
    )


def _without_site_names(
    shab_persons: list[ShabPerson],
    *,
    addresses: list[tuple[str, str]],
    domain: str | None,
    guess: str | None = None,
) -> Contact | None:
    """`guess` is where an info@ may be constructed; None constructs none."""
    # each person once, as the newest notice naming them has it; sorted() is
    # stable, so within a rank the newest claim stays in front
    ranked = sorted(shab_tool.current(shab_persons), key=lambda p: rank(p.role))
    chosen = ranked[0] if ranked else None
    inbox = _inbox(addresses, domain)

    if chosen is not None:
        # the same order as a name from the site: the published inbox, else
        # info@ guessed and marked (widened to SHAB names on 2026-09-24)
        if inbox:
            email, kind = inbox[0], "generic"
        elif guess is not None:
            email, kind = f"info@{guess}", "constructed"
        else:
            email, kind = None, None
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
    offsite = next(((e, u) for e, u in addresses if not _on_site(e, domain)), None)
    if offsite:
        return Contact(
            email=offsite[0],
            email_kind="third_party",
            source="site",
            source_url=offsite[1],
        )
    return None


_MAX_OFFERED = 8


def offered(
    contact: Contact, addresses: list[tuple[str, str]], domain: str | None
) -> list[ContactAddress]:
    """What the review card offers: the chosen address, then every other
    address the pages carry, each with its kind — a general inbox, someone's
    own on the site's domain, or third party on any other."""
    rows: list[ContactAddress] = []
    if contact.email and contact.email_kind:
        rows.append(ContactAddress(email=contact.email, kind=contact.email_kind))
    for email, _ in addresses:
        if any(r.email == email for r in rows):
            continue
        if not _on_site(email, domain):
            kind = "third_party"
        elif email.split("@", 1)[0] in _GENERIC:
            kind = "generic"
        else:
            kind = "seen"
        rows.append(ContactAddress(email=email, kind=kind))
    for row in rows:
        if row.kind != "third_party" and other_ending(email_domain(row.email), domain):
            row.note = f"the site's name under another ending than {domain}"
    return rows[:_MAX_OFFERED]


def _is_lead(result: Result, *, company: str, name: str | None) -> bool:
    parts = urlsplit(result.url)
    host = (parts.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return False
    if not parts.path.startswith("/in/"):
        return False  # a company page or a post is not a person
    title = normalise(result.title)
    # the company's longest word: "St. Galler Metallbau" -> "metallbau"
    marker = max(normalise(company).split(), key=len)
    if marker not in title:
        return False
    return name is None or normalise(name).split()[-1] in title


async def linkedin_lead(
    company: str, name: str | None, *, settings: Settings, search: Search
) -> str | None:
    """A LinkedIn profile URL found by a restricted web search, or None."""
    short = strip_legal_form(company)
    who = f' "{name}"' if name else ""
    query = f'site:linkedin.com/in{who} "{short}"'
    for result in await search(query, settings=settings):
        if _is_lead(result, company=short, name=name):
            return result.url
    return None


async def find_contact(
    state: dict[str, Any],
    *,
    settings: Settings,
    shab: Shab = shab_tool.persons,
    search: Search = search_tool.search_free,
) -> dict:
    """`shab` and `search` are passed in for the reason `enrich_company`
    takes `child`: a test can hand in a double and see whether it was asked
    at all."""
    site: SiteChoice = state["site"]
    profile: CompanyProfile = state["profile"]
    texts: dict[str, str] = state.get("page_texts") or {}
    domain = registered_domain(site.url)
    guess = inbox_domain(site.url)
    addresses = page_addresses(texts)

    # A lone first name cannot be greeted, and the mail treats it as nobody
    # named; so does this node, or it would guess an info@ for nobody.
    persons = [p for p in profile.persons if can_be_addressed(p.name)]
    if persons:
        ranked = sorted(persons, key=lambda p: (not _own_address(p), rank(p.role)))
        contact = _from_site(
            ranked[0],
            texts=texts,
            addresses=addresses,
            domain=domain,
            guess=guess,
            site_url=site.url,
        )
        contact.alternatives = [_describe(p.name, p.role, p.email) for p in ranked[1:]]
    else:
        found = await shab(state["uid"], settings=settings)
        # the rule check_profile applies to the site's names
        people = [
            p for p in found if names_a_person(p.name, company=state["company"].name)
        ]
        contact = _without_site_names(
            people, addresses=addresses, domain=domain, guess=guess
        )

    if contact is None or contact.email_kind in ("constructed", None):
        name = contact.name if contact else None
        lead = await linkedin_lead(
            state["company"].name, name, settings=settings, search=search
        )
        if contact is not None:
            contact.linkedin_lead = lead
        elif lead is not None:
            contact = Contact(source="site", linkedin_lead=lead)

    if contact is None:  # a finding: nobody named, no inbox, no lead
        return {"contact": None, "contact_id": None}
    contact.addresses = offered(contact, addresses, domain)

    with connect(settings.db_path) as conn:
        contact_id = record_contact(conn, state["run_id"], state["uid"], contact)
    return {"contact": contact, "contact_id": contact_id}
