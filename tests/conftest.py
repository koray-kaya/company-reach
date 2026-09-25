import http.server
import threading
from collections.abc import Callable, Iterator
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
    # Brave key arms the paid provider, SENDING_APPROVED unlocks Send, a
    # SEARXNG_URL moves search away from the host the tests mock, and
    # COMPANY_REACH_TRACING traces. A test that wants one sets its own.
    for name in (
        "BRAVE_SEARCH_API_KEY",
        "SENDING_APPROVED",
        "SEARXNG_URL",
        "COMPANY_REACH_TRACING",
    ):
        monkeypatch.delenv(name, raising=False)
    profile = tmp_path / "profile.toml"
    profile.write_text(profile_text())
    return Settings(_env_file=None, data_dir=tmp_path / "data", profile_path=profile)


@pytest.fixture
def traces_sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], list[str]]]:
    """Tracing switched on the way a shell does it — LANGSMITH_TRACING=true
    exported for another project — against a LangSmith stand-in on
    127.0.0.1 that records every request it gets. Yields a function that
    waits for LangChain's tracer threads and returns what reached the
    stand-in: "POST /runs/multipart" and the like, or nothing."""
    from langchain_core.tracers.langchain import wait_for_all_tracers
    from langsmith import run_trees
    from langsmith import utils as ls_utils

    received: list[str] = []

    class Sink(http.server.BaseHTTPRequestHandler):
        def _record(self) -> None:
            self.rfile.read(int(self.headers.get("content-length") or 0))
            received.append(f"{self.command} {self.path}")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = do_POST = do_PATCH = _record

        def log_message(self, *args) -> None:  # keep the test output clean
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("LANGSMITH_API_KEY", "not-a-real-key")
    # langsmith caches the environment it read and the client it built; both
    # are reset, so this test sees its own and leaves nothing to the next
    monkeypatch.setattr(run_trees, "_CLIENT", None)
    ls_utils.get_env_var.cache_clear()

    def sent() -> list[str]:
        wait_for_all_tracers()
        return list(received)

    yield sent
    ls_utils.get_env_var.cache_clear()
    server.shutdown()
    server.server_close()
