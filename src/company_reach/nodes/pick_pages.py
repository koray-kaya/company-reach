"""Which of a site's pages are worth reading.

`find_site` leaves a list of up to 200 URLs; `read_pages` fetches whatever
this node selects. The model is asked only when there is something to
choose: below the cap, reading every page is cheaper than a call whose
answer cannot be wrong, and most sites in this population are that small.

The model chooses only what is left. The home page and the first
Impressum, Kontakt and Über-uns page on the list are read whatever it
answers — the audit found that whether the Impressum was read depended on
the model alone, and a profile read without it names nobody. One of each,
because a site in four languages offers four of each and they say the same;
the model is shown the rest, told how many slots are free, and may still
choose a second Kontakt page. The words are the ones `find_site` uses to
find the Impressum in the first place.

The URLs are the site's own text. A path can carry a sentence — an injected
instruction fits in a slug as easily as in a paragraph — so the list is
delimited and labelled as data, the way page text is in `pick_site.md`, and
anything the model names that was not offered is dropped rather than
fetched.
"""

from typing import Any

from pydantic import BaseModel, Field

from company_reach.models import CompanyRecord
from company_reach.nodes.find_site import SiteChoice
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.candidate_pages import page_kind
from company_reach.tools.untrusted import as_data


class PageChoice(BaseModel):
    """What `pick_pages.md` returns."""

    urls: list[str] = Field(description="copied exactly from the offered list")


def _as_block(urls: list[str]) -> str:
    return as_data("\n".join(urls), label="PAGES")


def floor(offered: list[str], home: str) -> list[str]:
    """What is read whatever the model says: the home page, then the first
    Impressum, Kontakt and Über-uns page offered, in that order."""
    home = next((u for u in offered if u.rstrip("/") == home.rstrip("/")), home)
    first: dict[int, str] = {}
    for url in offered:
        kind = page_kind(url)
        if url != home and kind is not None and kind not in first:
            first[kind] = url
    return [home, *(first[kind] for kind in sorted(first))]


async def pick_pages(state: dict[str, Any], *, settings: Settings) -> dict:
    record: CompanyRecord = state["company"]
    site: SiteChoice = state["site"]
    offered: list[str] = state["page_urls"]
    limit = settings.max_pages_per_site

    if len(offered) <= limit:
        return {"pages_to_read": offered}

    chosen = floor(offered, site.url)[:limit]
    rest = [url for url in offered if url not in chosen]
    free = limit - len(chosen)
    if free <= 0 or not rest:
        return {"pages_to_read": chosen}

    answer, _ = await llm.ask(
        "pick_pages",
        PageChoice,
        settings=settings,
        name=record.name,
        seat=record.city or record.municipality,
        purpose=record.purpose_head,
        limit=str(free),
        pages=_as_block(rest),
    )

    # Offered-only, deduped, capped — the same shape of guard `score_pool`
    # puts on the UID set it gets back. The floor always holds the home
    # page, so "none of these" still reads it: by this point the site is
    # verified, and reading nothing would discard a company we know we found.
    allowed = set(rest)
    for url in answer.urls:
        if url in allowed and url not in chosen:
            chosen.append(url)
    return {"pages_to_read": chosen[:limit]}
