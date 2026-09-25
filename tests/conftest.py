from pathlib import Path

import pytest
from fictional_profile import profile_text

from company_reach.settings import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at a temporary data dir and profile; no .env is read.
    The profile carries a fictional [sender] and [invitation], so a test that
    drafts has what drafting needs."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("SEARCH_RETRY_PAUSE_S", "0")
    monkeypatch.setenv("LLM_RETRY_PAUSE_S", "0")
    # A key exported in the developer's shell would arm the paid provider in
    # every test; a test that wants Brave sets its own.
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    profile = tmp_path / "profile.toml"
    profile.write_text(profile_text())
    return Settings(_env_file=None, data_dir=tmp_path / "data", profile_path=profile)
