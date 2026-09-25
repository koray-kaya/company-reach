"""The harness of the opt-in evaluations in `test_prompts.py`, tested
without a model: these run in every suite, the evaluations only on demand."""

from pathlib import Path

EVALS = Path(__file__).parent / "test_prompts.py"


def test_no_evaluation_builds_its_own_settings():
    """Every evaluation takes the `eval_settings` fixture, whose database is
    a scratch copy. A `Settings()` of its own would point at
    data/company_reach.db again (audit: the draft evaluation wrote drafts
    with real names there)."""
    source = EVALS.read_text(encoding="utf-8")
    assert source.count("Settings()") == 1  # the fixture's own
    assert "def eval_settings(" in source
