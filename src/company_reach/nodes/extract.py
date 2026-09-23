"""The pages we read, turned into a profile of the company.

This is where untrusted text stops being something we merely showed a model
and becomes data the tool will act on: a person's name, a role, an e-mail
address a human is later invited to write to. The audit's one P1 lives on
this edge (`audit-2026-09-19.md:177`), and the defence is in two halves —
here the text is delimited and labelled as data, and in `check_profile`
every name and address that comes back is checked against the page it was
supposed to come from.

Two things this node deliberately does not do. It does not store: the
`profiles` table belongs to `check_profile`, so no row can hold an unchecked
profile, not even between two nodes of a run that crashed. And it does not
return a `CompanyProfile`: it returns the `RawProfile` the model produced,
so nothing downstream can mistake an unchecked answer for a checked one —
`graph.spec.yaml:160` has this node writing `profile`, and splitting it is
the one place this milestone departs from the spec on purpose.
"""

from typing import Any

from company_reach.errors import FetchError
from company_reach.models import CompanyRecord, RawProfile
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.untrusted import as_data


def _as_blocks(texts: dict[str, str]) -> str:
    """One delimited block per page, each carrying its own url — the url is
    how `check_profile` can later say which page an address was taken from,
    and how the reviewer knows which page to open."""
    return "\n\n".join(as_data(text, url=url) for url, text in texts.items())


async def extract(state: dict[str, Any], *, settings: Settings) -> dict:
    record: CompanyRecord = state["company"]
    texts: dict[str, str] = state["page_texts"]

    if not texts:
        # The site was found and verified, so reading none of its pages is
        # infrastructure failing rather than a fact about the company. The
        # wrapper turns this into an error row a later run retries; an empty
        # profile would be written down as the truth about the company.
        raise FetchError(
            f"no page of {record.name}'s site could be read, so there is "
            "nothing to extract from"
        )

    answer, _ = await llm.ask(
        "extract",
        RawProfile,
        settings=settings,
        name=record.name,
        uid=record.uid_dotted,
        seat=record.city or record.municipality,
        purpose=record.purpose_head,
        pages=_as_blocks(texts),
    )
    return {"raw_profile": answer}
