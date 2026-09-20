"""Golden-set evaluation. Opt-in, because it calls the real endpoint.

    RUN_LLM_EVALS=1 uv run pytest tests/test_prompts.py -s

What it measures, and why these numbers and not others:

*top-5 and top-10 overlap* — M3 draws from the top of the ranking, so only
the top matters. A prompt change that reshuffles the bottom of the pool is
not a regression.

*bias* — mean(model - human). A systematically generous prompt fills the
batch with companies the reviewer will reject, which is the expensive kind of
error: each one costs a web search, ten page fetches and a draft.

*exact agreement* is reported but not asserted. On a 0-10 scale two careful
people rarely agree exactly, and the measured model-against-itself noise is
already 45/50 — an exact-match threshold would mostly measure noise.

The baseline below was measured on 2026-09-20 with prompt score@1 and the
goal at that date. Raise it when a change genuinely improves the numbers;
that is the point of having it.
"""

import json
import os
import statistics
from pathlib import Path

import pytest

from company_reach.models import CompanyRecord, ScoreBatch
from company_reach.nodes.score_pool import _as_prompt_data, _check
from company_reach.nodes.write_criteria import format_criteria, write_criteria
from company_reach.profile import load_profile
from company_reach.settings import Settings
from company_reach.tools import llm

GOLDEN = Path("data/golden/labels.jsonl")

# Measured 2026-09-20, goal "make or process a product and sell it on",
# score@1, GLM-5.3-Flash, reasoning_effort=low.
BASELINE = {"top5": 3, "top10": 6, "abs_bias": 0.37}

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_EVALS") != "1",
    reason="calls the real model endpoint; set RUN_LLM_EVALS=1",
)


def _top(scores: dict[str, int], n: int) -> set[str]:
    return {u for u, _ in sorted(scores.items(), key=lambda z: (-z[1], z[0]))[:n]}


async def test_scoring_matches_the_hand_labels():
    if not GOLDEN.is_file():
        pytest.skip(f"no golden set at {GOLDEN} (it lives outside git)")

    lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    items = [json.loads(line) for line in lines]
    human = {i["uid"]: i["label"] for i in items}
    companies = [
        CompanyRecord(
            uid=i["uid"],
            name=i["name"],
            legal_form="0106",
            municipality="3203",
            purpose=i["purpose"],
            purpose_head=i["purpose"],
        )
        for i in items
    ]

    settings = Settings()
    goal = load_profile(Path("profile.toml")).goal
    criteria, _ = await write_criteria(goal, settings=settings)
    answer, prov = await llm.ask(
        "score",
        ScoreBatch,
        settings=settings,
        goal=goal,
        criteria=format_criteria(criteria),
        companies=_as_prompt_data(companies),
    )
    valid, missing, dropped = _check(answer, {c.uid: c for c in companies})
    model = {v.uid: v.score for v in valid}

    shared = [u for u in human if u in model]
    top5 = len(_top(human, 5) & _top(model, 5))
    top10 = len(_top(human, 10) & _top(model, 10))
    bias = statistics.mean(model[u] - human[u] for u in shared)
    exact = sum(1 for u in shared if human[u] == model[u])

    print(
        f"\ngolden set: {len(shared)} companies, prompt score@{prov.prompt_version}, "
        f"{prov.seconds:.0f}s\n"
        f"  top-5 overlap  {top5}/5   (baseline {BASELINE['top5']})\n"
        f"  top-10 overlap {top10}/10  (baseline {BASELINE['top10']})\n"
        f"  bias           {bias:+.2f}  (baseline {BASELINE['abs_bias']:+.2f})\n"
        f"  exact          {exact}/{len(shared)}  (reported, not asserted)\n"
        f"  missing {len(missing)}, dropped {dropped}"
    )

    assert not missing, "the model failed to answer for some companies"
    assert top5 >= BASELINE["top5"], f"top-5 overlap fell to {top5}"
    assert top10 >= BASELINE["top10"], f"top-10 overlap fell to {top10}"
    assert abs(bias) <= BASELINE["abs_bias"] + 0.5, f"bias drifted to {bias:+.2f}"
