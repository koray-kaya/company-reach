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
    # A setting exported in the developer's shell would reach every test: a
    # Brave key arms the paid provider, SENDING_APPROVED unlocks Send, and a
    # SEARXNG_URL moves search away from the host the tests mock. A test
    # that wants one sets its own.
    for name in ("BRAVE_SEARCH_API_KEY", "SENDING_APPROVED", "SEARXNG_URL"):
        monkeypatch.delenv(name, raising=False)
    profile = tmp_path / "profile.toml"
    profile.write_text(profile_text())
    return Settings(_env_file=None, data_dir=tmp_path / "data", profile_path=profile)
