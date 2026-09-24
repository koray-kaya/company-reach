from pathlib import Path

import pytest

from company_reach.settings import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at a temporary data dir and profile; no .env is read."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("SEARCH_RETRY_PAUSE_S", "0")
    profile = tmp_path / "profile.toml"
    profile.write_text(
        'goal = "Firms that make things."\nsurvey_url = "https://survey.example/form"\n'
    )
    return Settings(_env_file=None, data_dir=tmp_path / "data", profile_path=profile)
