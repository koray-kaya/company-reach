"""Turn one sentence of goal into criteria a scorer can apply.

A separate step, rather than one long scoring prompt, for two reasons. The
criteria are shown before any scoring is paid for, so a goal that was read
the wrong way costs seconds instead of twenty minutes. And they are stored,
one set per goal, so months later the question "why did this company score
8?" has an answer that is not "the model decided"."""

import hashlib

from company_reach.models import SelectionCriteria
from company_reach.settings import Settings
from company_reach.tools import llm


async def write_criteria(
    goal: str, *, settings: Settings
) -> tuple[SelectionCriteria, llm.Provenance]:
    return await llm.ask("criteria", SelectionCriteria, settings=settings, goal=goal)


def format_criteria(criteria: SelectionCriteria) -> str:
    """One block of text for the prompt and for the screen. The scorer sees
    exactly what the user reviewed."""
    lines: list[str] = []
    for field in ("must", "must_not", "positive_signals"):
        lines.append(f"{field}:")
        for entry in getattr(criteria, field):
            lines.append(f"  - {entry}")
    return "\n".join(lines)


def criteria_hash(criteria: SelectionCriteria) -> str:
    """Names a set of criteria on every score made against it. Taken over
    the text the scorer is shown, so two sets that read the same are one."""
    return hashlib.sha256(format_criteria(criteria).encode("utf-8")).hexdigest()[:12]
