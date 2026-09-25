"""Score the screened pool against the goal, in batches, resumably.

Three ideas hold this together.

*The cache.* A score belongs to a goal, a prompt version, a model and the
criteria it was scored against. Rerun with the same four and nothing is asked
again; change any of them and the answer is rightly recomputed. This is what
makes `--limit` safe: score two hundred now, two hundred more tonight,
nothing repeated, all against one set of rules.

*The shuffle.* Swiss UIDs run in registration order, so taking the first N
companies would sample the oldest firms in the canton. One seeded shuffle
makes any partial pass a representative sample — and it is why `draw_batch`
in M3 needs no tie-breaking of its own.

*Distrust of the answer.* The model may drop a company, answer twice for one,
invent a UID that was never sent, or return a score outside 0-10. Structured
output prevents none of those: it constrains shape, not content. Every batch
is therefore checked against the UIDs that were sent.
"""

import asyncio
import random
import time
from dataclasses import dataclass

from company_reach.models import CompanyRecord, Score, ScoreBatch, SelectionCriteria
from company_reach.nodes.write_criteria import criteria_hash, format_criteria
from company_reach.profile import goal_hash
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import (
    connect,
    count_scored,
    unscored_companies,
    upsert_scores,
)


@dataclass(frozen=True)
class ScoreReport:
    scored: int
    cached: int
    dropped: int
    failed_batches: int
    seconds: float


def _as_prompt_data(companies: list[CompanyRecord]) -> str:
    """Only the three fields the scorer may use. Sending the whole record
    would invite the model to reason from the address or the legal form."""
    import json

    return json.dumps(
        [
            {"uid": c.uid, "name": c.name, "purpose": c.purpose_head[:300]}
            for c in companies
        ],
        ensure_ascii=False,
    )


def _check(
    answer: ScoreBatch, sent: dict[str, CompanyRecord]
) -> tuple[list[Score], set[str], int]:
    """Split an answer into (valid scores, uids still missing, dropped count)."""
    valid: list[Score] = []
    seen: set[str] = set()
    dropped = 0
    for entry in answer.scores:
        if entry.uid not in sent:  # invented, or from another batch
            dropped += 1
            continue
        if entry.uid in seen:  # answered twice
            dropped += 1
            continue
        if not 0 <= entry.score <= 10:
            dropped += 1
            continue
        seen.add(entry.uid)
        valid.append(Score(uid=entry.uid, score=entry.score, reason=entry.reason))
    return valid, set(sent) - seen, dropped


async def _score_batch(
    batch: list[CompanyRecord], goal: str, criteria_text: str, *, settings: Settings
) -> tuple[list[Score], int]:
    """One batch, with a single re-ask for whatever came back missing."""
    sent = {c.uid: c for c in batch}
    answer, _ = await llm.ask(
        "score",
        ScoreBatch,
        settings=settings,
        goal=goal,
        criteria=criteria_text,
        companies=_as_prompt_data(batch),
    )
    valid, missing, dropped = _check(answer, sent)

    if missing:
        retry = [sent[uid] for uid in sorted(missing)]
        answer, _ = await llm.ask(
            "score",
            ScoreBatch,
            settings=settings,
            goal=goal,
            criteria=criteria_text,
            companies=_as_prompt_data(retry),
        )
        more, still_missing, more_dropped = _check(answer, {c.uid: c for c in retry})
        valid.extend(more)
        dropped += more_dropped
        if still_missing:
            # Not an error: the company is simply left unscored and will be
            # picked up by the next run, because the cache only records
            # answers that arrived.
            dropped += 0
    return valid, dropped


async def score_pool(
    run_id: str,
    goal: str,
    criteria: SelectionCriteria,
    *,
    settings: Settings,
    limit: int | None = None,
    seed: int = 0,
) -> ScoreReport:
    started = time.monotonic()
    prompt_version, _ = llm.load_prompt("score")
    key = dict(
        goal_hash=goal_hash(goal),
        prompt_version=prompt_version,
        model=settings.llm_model,
        criteria_hash=criteria_hash(criteria),
    )

    with connect(settings.db_path) as conn:
        candidates = unscored_companies(conn, **key)
        already = count_scored(conn, **key)

    random.Random(seed).shuffle(candidates)
    candidates = candidates[: (limit if limit is not None else settings.score_limit)]

    size = settings.score_batch_size
    batches = [candidates[i : i + size] for i in range(0, len(candidates), size)]
    criteria_text = format_criteria(criteria)

    results = await asyncio.gather(
        *[
            _score_batch(batch, goal, criteria_text, settings=settings)
            for batch in batches
        ],
        return_exceptions=True,
    )

    scored: list[Score] = []
    dropped = 0
    failed = 0
    for result in results:
        if isinstance(result, BaseException):
            failed += 1  # one bad batch must not lose the others
            continue
        batch_scores, batch_dropped = result
        scored.extend(batch_scores)
        dropped += batch_dropped

    if scored:
        with connect(settings.db_path) as conn:
            upsert_scores(conn, scored, **key)

    return ScoreReport(
        scored=len(scored),
        cached=already,
        dropped=dropped,
        failed_batches=failed,
        seconds=time.monotonic() - started,
    )
