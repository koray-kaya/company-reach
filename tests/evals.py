"""What the opt-in evaluations in `test_prompts.py` share: the production
decision they grade, the spread across trials, and the file they append
their results to. No model is called here, so `test_evals.py` tests it in
every suite."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# One JSON line per evaluation run, under the gitignored data/: the
# per-company numbers of the private set stay on this machine.
RESULTS = Path("data/evals/results.jsonl")


@dataclass(frozen=True)
class Decision:
    """The production decision on one trial. A run draws a company when
    its score is at least the draw threshold; a company is worth drawing
    when its human label is at least the human bar."""

    drawn: int
    deserved: int
    hits: int  # drawn and worth drawing

    @property
    def precision(self) -> float:
        """Of the companies drawn, the share worth it. Drawing nothing is
        0, not undefined: a run that draws nothing finds nobody."""
        return self.hits / self.drawn if self.drawn else 0.0

    @property
    def recall(self) -> float:
        """Of the companies worth drawing, the share drawn."""
        return self.hits / self.deserved


def decision(
    model: dict[str, int], human: dict[str, int], *, draw_at: int, human_bar: int
) -> Decision:
    """Grade the model's scores the way a run uses them. A company the
    model left out of its answer is not drawn, so it counts against
    recall."""
    deserved = {uid for uid, label in human.items() if label >= human_bar}
    if not deserved:
        raise ValueError("no labelled company is worth drawing; nothing to grade")
    drawn = {uid for uid, score in model.items() if score >= draw_at and uid in human}
    return Decision(
        drawn=len(drawn), deserved=len(deserved), hits=len(drawn & deserved)
    )


def spread(values: list[float]) -> tuple[float, float]:
    """The worst and the best trial. Three trials are too few for a
    standard deviation to mean much; the range says what one run can be."""
    return min(values), max(values)


def append_result(path: Path, record: dict[str, Any]) -> None:
    """One line per evaluation run. Appended, never rewritten, so the file
    is the history of every measurement, failed ones included."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
