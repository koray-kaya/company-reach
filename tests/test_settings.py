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


def test_search_defaults_to_the_published_searxng_port(monkeypatch, tmp_path: Path):
    """Every command runs under `uv` on the host since the review page left
    Docker (decided 2026-09-25), and the container's name resolves only
    inside Docker: the default is the port compose publishes on loopback."""
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    assert Settings(_env_file=None, data_dir=tmp_path).searxng_url == (
        "http://127.0.0.1:8080"
    )
    example = Settings(_env_file=".env.example", data_dir=tmp_path)
    assert example.searxng_url == "http://127.0.0.1:8080"


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


def test_only_the_projects_own_variable_turns_tracing_on(monkeypatch, tmp_path: Path):
    """LANGSMITH_TRACING is LangChain's own switch too, so exported in the
    shell for any other project it set this setting as well (audit). The
    setting reads a name no other project exports."""
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("COMPANY_REACH_TRACING", raising=False)
    assert Settings(_env_file=None, data_dir=tmp_path).langsmith_tracing is False

    monkeypatch.setenv("COMPANY_REACH_TRACING", "true")
    assert Settings(_env_file=None, data_dir=tmp_path).langsmith_tracing is True

    # and .env.example names the variable that is read
    monkeypatch.delenv("COMPANY_REACH_TRACING")
    example = Settings(_env_file=".env.example", data_dir=tmp_path)
    assert example.langsmith_tracing is False
    assert "\nCOMPANY_REACH_TRACING=false\n" in Path(".env.example").read_text()


def test_the_example_env_arms_no_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    s = Settings(_env_file=".env.example", data_dir=tmp_path)
    assert s.brave_search_api_key is None


def test_no_playwright_setting_is_left():
    """Audit: PLAYWRIGHT_URL was read, documented and switched nothing on —
    no Playwright service exists. It returns with the service, if ever."""
    assert "playwright_url" not in Settings.model_fields
    assert "PLAYWRIGHT" not in Path(".env.example").read_text()
