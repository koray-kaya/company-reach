"""The harness of the opt-in evaluations in `test_prompts.py`, tested
without a model: these run in every suite, the evaluations only on demand."""

import json
from pathlib import Path

import pytest
from evals import RESULTS, append_result, decision, spread

EVALS = Path(__file__).parent / "test_prompts.py"


def test_no_evaluation_builds_its_own_settings():
    """Every evaluation takes the `eval_settings` fixture, whose database is
    a scratch copy. A `Settings()` of its own would point at
    data/company_reach.db again (audit: the draft evaluation wrote drafts
    with real names there)."""
    source = EVALS.read_text(encoding="utf-8")
    assert source.count("Settings()") == 1  # the fixture's own
    assert "def eval_settings(" in source


# --- the production decision -------------------------------------------------


def test_the_decision_is_graded_at_the_draw_threshold():
    """A company is drawn at score >= 7 and worth drawing at a human label
    >= 6: precision is how many drawn were worth it, recall how many worth
    it were drawn (audit: a bias near zero hid large errors both ways)."""
    model = {"a": 9, "b": 7, "c": 6, "d": 8}
    human = {"a": 9, "b": 2, "c": 8, "d": 6}

    d = decision(model, human, draw_at=7, human_bar=6)

    assert (d.drawn, d.deserved, d.hits) == (3, 3, 2)
    assert d.precision == pytest.approx(2 / 3)
    assert d.recall == pytest.approx(2 / 3)


def test_a_company_the_model_left_out_is_not_drawn():
    d = decision({"a": 9}, {"a": 9, "b": 8}, draw_at=7, human_bar=6)
    assert d.recall == 0.5
    assert d.precision == 1.0


def test_drawing_nothing_is_no_precision_at_all():
    d = decision({"a": 3, "b": 2}, {"a": 9, "b": 1}, draw_at=7, human_bar=6)
    assert d.precision == 0.0
    assert d.recall == 0.0


def test_a_set_with_nothing_worth_drawing_cannot_grade_the_decision():
    with pytest.raises(ValueError, match="worth drawing"):
        decision({"a": 9}, {"a": 2}, draw_at=7, human_bar=6)


def test_the_spread_is_the_worst_and_the_best_trial():
    assert spread([0.8, 1.0, 0.9]) == (0.8, 1.0)


def test_results_are_appended_one_line_per_evaluation(tmp_path):
    path = tmp_path / "evals" / "results.jsonl"  # the folder does not exist yet

    append_result(path, {"eval": "scoring", "trials": [{"recall": 0.9}]})
    append_result(path, {"eval": "scoring", "note": "Käserei"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["eval"] for line in lines] == ["scoring", "scoring"]
    assert "Käserei" in lines[1]  # readable, not escaped


def test_the_results_file_is_under_data():
    """data/ is gitignored: the per-company numbers stay on this machine."""
    assert Path("data/evals/results.jsonl") == RESULTS
