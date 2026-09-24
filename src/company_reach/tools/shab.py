"""SHAB, the Swiss Official Gazette of Commerce: who the register named.

Two calls, because the list never carries the notice text. The list call
filters by company; then each notice's XML is fetched and its person blocks
are parsed. httpx does the HTTP and the standard library's ElementTree reads
the XML. SHAB's terms ask for the source to be cited and for nothing to look
like an official publication — see NOTICE.

The filter is the part with history. `uids=` in the dotted form is the only
spelling that filters: `uid=` is silently ignored and answers with the whole
gazette, and `CHE000000003` silently answers with nothing
(`data-sources.md` §B2). Two guards follow from that: a total no single
company could have means the filter was ignored, and a notice that does not
mention the company is dropped.

What the register says is a past state. Every person carries the date of the
notice that named them, and entries a notice strikes out are marked as such.
"""

import re
import xml.etree.ElementTree as ET
from typing import NamedTuple

import httpx

from company_reach import __version__
from company_reach.errors import ShabError
from company_reach.models import ShabPerson, dotted_uid
from company_reach.settings import Settings

_UA = f"company-reach/{__version__} (+https://github.com/koray-kaya/company-reach)"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# A company founded in 2003 had 24 notices since 2018. A total in the
# hundreds is not one company, it is a filter that was not applied.
_MAX_NOTICES = 500

# The headers that open a person block, German and French. Everything up to
# the colon belongs to the header ("... und erloschene Unterschriften").
_HEADER = re.compile(
    r"((?:Neu )?[Ee]ingetragene Person(?:en)?[^:.;]*"
    r"|Ausgeschiedene Person(?:en)?[^:.;]*"
    r"|Gelöschte Person(?:en)?[^:.;]*"
    r"|Personnes? (?:inscrites?|radiées?)[^:.;]*"
    r"|Nouvelles? personnes? inscrites?[^:.;]*):\s*"
)
_DEPARTED = ("Ausgeschiedene", "Gelöschte", "radiée")

# Parts of an entry that are neither the name nor the function: origin,
# residence, citizenship, signature, shares.
_DETAIL = re.compile(
    r"^(?:von|de|d'|in|à|aus|mit|avec|ohne|sans|maintenant|neu|nun)\b"
    r"|unterschrift|prokura|signature|procuration|stammanteil|\bparts?\b"
    r"|staatsangehörig|ressortissant|citoyen|nationalité",
    re.IGNORECASE,
)
# Enough of the German role vocabulary to tell "Beispiel Hans, Direktor"
# (name, role) from "Beispiel, Hans" (surname, given name), and a role from a
# second place of origin ("von Wil SG, Gossau SG und Flawil"). French roles
# are written in lower case and need no list. A German role missing here
# costs the role, never the person.
_ROLE_WORDS = re.compile(
    r"Verwaltungsrat|Geschäftsführ|Geschäftsleitung|Gesellschafter|Direktor"
    r"|Präsident|Mitglied|Liquidator|Inhaber|Vorsitz|Delegierte|Leiter|Aktuar"
    r"|Sekretär"
)
# Companies sit in person blocks too: the audit firm, by UID or by the older
# register number, and associations holding a seat.
_COMPANY = re.compile(r"\bCHE?-\d|Revisionsstelle|organe de révision")
_HISTORY = re.compile(r"\s*\[[^\]]*\]")  # "[bisher: ...]"


class Entry(NamedTuple):
    name: str
    role: str | None
    departed: bool


def _is_role(field: str) -> bool:
    return not _DETAIL.search(field) and (
        bool(_ROLE_WORDS.search(field)) or field[:1].islower()
    )


def _is_given_name(field: str) -> bool:
    return (
        field[:1].isupper()
        and not _DETAIL.search(field)
        and not _ROLE_WORDS.search(field)
    )


def _entry(text: str, *, departed: bool) -> Entry | None:
    if _COMPANY.search(text):
        return None
    fields = [f.strip() for f in text.split(",") if f.strip()]
    if not fields:
        return None
    # "Surname, Given, ..." in most German notices; "Surname Given, ..." in
    # FR registries and some bilingual ones, where the parts cannot be told
    # apart and the name is kept as written.
    if len(fields) > 1 and _is_given_name(fields[1]):
        name, rest = f"{fields[1]} {fields[0]}", fields[2:]
    else:
        name, rest = fields[0], fields[1:]
    if "(" in name:  # "Beispiel Turnverein (nicht ... eingetragener Verein)"
        return None
    role = ", ".join(f for f in rest if _is_role(f)) or None
    return Entry(" ".join(name.split()), role, departed)


def parse_persons(text: str) -> list[Entry]:
    """The people in one notice's headed person blocks, in notice order.

    Only headed blocks are read. VD, GE and NE write persons as prose
    ("Signature individuelle est conférée à ..."), and a notice like that
    yields nothing here rather than a guess.
    """
    parts = _HEADER.split(text)
    found: list[Entry] = []
    # split() with one group gives [before, header, block, header, block, ...]
    for header, block in zip(parts[1::2], parts[2::2], strict=True):
        departed = any(word in header for word in _DEPARTED)
        block = _HISTORY.sub("", block).strip().rstrip(".")
        for raw in block.split(";"):
            entry = _entry(raw, departed=departed)
            if entry is not None:
                found.append(entry)
    return found


async def _get(client: httpx.AsyncClient, url: str, **params) -> httpx.Response:
    try:
        response = await client.get(url, params=params or None)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise ShabError(f"SHAB request failed: {url}: {error}") from error
    return response


async def persons(
    uid: str, *, settings: Settings, client: httpx.AsyncClient | None = None
) -> list[ShabPerson]:
    """Everyone every HR notice about this company names, newest notice first.

    `[]` when the company has no notices or none names a person — a finding.
    `ShabError` when SHAB could not be asked, or its answer shows the filter
    was ignored.
    """
    base = settings.shab_url.rstrip("/")
    dotted = dotted_uid(uid)
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA})
    try:
        listing = (
            await _get(
                client,
                f"{base}/publications",
                publicationStates="PUBLISHED",
                rubrics="HR",
                uids=dotted,
                **{"pageRequest.size": _MAX_NOTICES},
            )
        ).json()
        if listing.get("total", 0) > _MAX_NOTICES:
            raise ShabError(
                f"SHAB answered {listing['total']} notices for {dotted}; "
                "the uids filter was not applied"
            )

        found: list[ShabPerson] = []
        for item in listing.get("content", []):
            url = f"{base}/publications/{item['meta']['id']}/xml"
            response = await _get(client, url)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as error:
                raise ShabError(f"SHAB notice is not XML: {url}") from error
            text = root.findtext("content/publicationText") or ""
            if dotted not in text:
                continue
            published = root.findtext("meta/publicationDate") or ""
            found.extend(
                ShabPerson(
                    name=e.name,
                    role=e.role,
                    departed=e.departed,
                    published=published,
                    source_url=url,
                )
                for e in parse_persons(text)
            )
        return found
    finally:
        if own:
            await client.aclose()
