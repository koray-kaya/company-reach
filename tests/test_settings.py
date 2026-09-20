from pathlib import Path

from company_reach.settings import Settings


def test_defaults_and_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("MAX_PAGES_PER_SITE", "12")
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.max_pages_per_site == 12
    assert s.score_batch_size == 50
    assert s.llm_structured_method == "json_schema"
    assert "abc" not in repr(s)  # SecretStr keeps the key out of logs
    assert s.db_path == tmp_path / "company_reach.db"


def test_drawing_defaults(monkeypatch, tmp_path: Path):
    """Ten because review starts once ten candidates are ready (design §31);
    seven because it is the gap between what Koray rated well (model 9) and
    what he rated junk (model 6 at most)."""
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.batch_size == 10
    assert s.draw_min_score == 7
