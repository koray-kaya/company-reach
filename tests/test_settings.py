from pathlib import Path

import pytest

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


def test_the_brave_key_is_read(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test-key")
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.brave_search_api_key.get_secret_value() == "brave-test-key"
    assert "brave-test-key" not in repr(s)


@pytest.mark.parametrize(
    "raw", ["", "   ", "# optional, used only when SearXNG errors"]
)
def test_an_empty_brave_key_is_no_key(monkeypatch, tmp_path: Path, raw):
    """Audit H11: python-dotenv keeps a trailing comment as the value, and
    a key that is not None arms the paid provider."""
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", raw)
    assert Settings(_env_file=None, data_dir=tmp_path).brave_search_api_key is None


def test_the_example_env_arms_no_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_URL", raising=False)
    s = Settings(_env_file=".env.example", data_dir=tmp_path)
    assert s.brave_search_api_key is None
    assert s.playwright_url is None
