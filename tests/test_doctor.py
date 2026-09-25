import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.tools.doctor import run_checks

URL = "https://api.openai.com/v1/chat/completions"
runner = CliRunner()
SEARX = "http://searxng:8080"
BRAVE = "https://api.search.brave.com/res/v1/web/search"
REAL_SURVEY = "https://umfrage.beispiel-hochschule.ch/kmu"


@pytest.fixture
def settings(settings):
    """No pause between search queries: doctor's search probe runs in every
    test here, and the politeness gap would only slow the suite."""
    return settings.model_copy(update={"search_gap_s": 0.0})


def _search_ok():
    respx.get(f"{SEARX}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://www.example-register.ch/",
                        "title": "t",
                        "content": "c",
                        "engine": "duckduckgo",
                    }
                ],
                "unresponsive_engines": [],
            },
        )
    )


def _config(enabled: list[str]):
    respx.get(f"{SEARX}/config").mock(
        return_value=httpx.Response(
            200, json={"engines": [{"name": n, "enabled": True} for n in enabled]}
        )
    )


def _real_survey(settings):
    settings.profile_path.write_text(
        f'goal = "Firms that make things."\nsurvey_url = "{REAL_SURVEY}"\n'
    )


def answer(content: str, *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "model": "GLM-5.3-Flash",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )


def probe_ok(marker: str = "COMPANY-REACH-OK") -> httpx.Response:
    return answer(json.dumps({"marker": marker}))


def probe_truncated() -> httpx.Response:
    return answer('{"mark', finish_reason="length")


@respx.mock
async def test_all_checks_pass(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "mojeek", "brave"])
    _real_survey(settings)
    checks = await run_checks(settings)
    assert [c.name for c in checks] == [
        "settings",
        "prompts",
        "database",
        "profile",
        "retention",
        "search",
        "engines",
        "brave",
        "endpoint",
        "token budget",
    ]
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


@respx.mock
async def test_a_failing_check_does_not_stop_the_others(settings):
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    checks = await run_checks(settings)
    assert len(checks) == 10  # every check still ran
    by_name = {c.name: c for c in checks}
    assert by_name["settings"].ok
    assert by_name["database"].ok
    assert not by_name["endpoint"].ok


@respx.mock
async def test_ignored_token_budget_is_a_failure(settings):
    """If a tiny max_tokens does not truncate, the endpoint is ignoring the
    budget — and a real run would have no protection against a runaway."""
    respx.post(URL).mock(side_effect=[probe_ok(), probe_ok()])
    checks = await run_checks(settings)
    budget = next(c for c in checks if c.name == "token budget")
    assert not budget.ok
    assert "ignored" in budget.detail


@respx.mock
async def test_wrong_marker_is_a_failure(settings):
    respx.post(URL).mock(side_effect=[probe_ok("SOMETHING-ELSE"), probe_truncated()])
    checks = await run_checks(settings)
    endpoint = next(c for c in checks if c.name == "endpoint")
    assert not endpoint.ok


@respx.mock
def test_cli_exits_zero_when_healthy(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "mojeek", "brave"])
    _real_survey(settings)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output


@respx.mock
def test_cli_exits_one_when_something_is_wrong(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    respx.post(URL).mock(return_value=httpx.Response(500))
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL" in result.output


@respx.mock
async def test_a_profile_without_a_survey_url_fails(settings):
    # every invitation carries the link; a run that drafts without one
    # would stop at the first company
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    settings.profile_path.write_text('goal = "Firms that make things."\n')
    profile = next(c for c in await run_checks(settings) if c.name == "profile")
    assert not profile.ok
    assert "survey_url" in profile.detail


@respx.mock
async def test_a_missing_profile_fails_without_stopping_the_others(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    settings.profile_path.unlink()
    checks = await run_checks(settings)
    assert len(checks) == 10
    assert not next(c for c in checks if c.name == "profile").ok


@respx.mock
async def test_retention_reports_what_a_purge_would_remove(settings):
    # informational: an old company is not a reason to refuse a run
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    retention = next(c for c in await run_checks(settings) if c.name == "retention")
    assert retention.ok
    assert "365 days" in retention.detail


@respx.mock
async def test_a_missing_baseline_engine_fails(settings):
    """Round-1 live run: mojeek and startpage were inactive in the pinned
    SearXNG, so the guard for "every baseline engine is down" could never
    fire."""
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "brave"])
    checks = {c.name: c for c in await run_checks(settings)}
    assert checks["engines"].ok is False
    assert "mojeek" in checks["engines"].detail


@respx.mock
async def test_search_that_answers_nothing_fails(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    respx.get(f"{SEARX}/search").mock(
        return_value=httpx.Response(
            200, json={"results": [], "unresponsive_engines": []}
        )
    )
    _config(["duckduckgo", "mojeek", "brave"])
    checks = {c.name: c for c in await run_checks(settings)}
    assert checks["search"].ok is False


@respx.mock
async def test_the_placeholder_survey_url_fails(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "mojeek", "brave"])
    checks = {c.name: c for c in await run_checks(settings)}
    assert checks["profile"].ok is False
    assert "placeholder" in checks["profile"].detail


def test_every_prompt_a_run_uses_is_loaded():
    from company_reach.tools import doctor

    assert set(doctor._PROMPTS) >= {
        "criteria",
        "score",
        "pick_site",
        "pick_pages",
        "extract",
        "draft",
    }


@respx.mock
async def test_the_settings_line_shows_the_gates(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    checks = {c.name: c for c in await run_checks(settings)}
    assert "sending_approved=False" in checks["settings"].detail
    assert "paid_fallback=none" in checks["settings"].detail


def test_the_settings_line_names_brave_when_a_key_is_set(settings):
    from pydantic import SecretStr

    from company_reach.tools.doctor import _settings_check

    keyed = settings.model_copy(update={"brave_search_api_key": SecretStr("k")})
    assert "paid_fallback=brave" in _settings_check(keyed).detail


@respx.mock
async def test_without_a_brave_key_the_check_says_so(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    brave = respx.get(BRAVE)
    checks = {c.name: c for c in await run_checks(settings)}
    assert checks["brave"].ok
    assert checks["brave"].detail == "not configured"
    assert not brave.called


@respx.mock
async def test_an_invalid_brave_key_fails(settings):
    """Review focus 5. The key is asked directly, not through search: a
    SearXNG that answers would otherwise hide a rejected key until the first
    company Brave has to confirm."""
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"brave_search_api_key": SecretStr("bad")})
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "mojeek", "brave"])
    respx.get(BRAVE).mock(return_value=httpx.Response(401))
    checks = {c.name: c for c in await run_checks(keyed)}
    assert checks["search"].ok
    assert checks["brave"].ok is False
    assert "BRAVE_SEARCH_API_KEY" in checks["brave"].detail


@respx.mock
async def test_a_working_brave_key_passes(settings):
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"brave_search_api_key": SecretStr("good")})
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    respx.get(BRAVE).mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"url": "https://www.example-register.ch/", "title": "t"}
                    ]
                }
            },
        )
    )
    checks = {c.name: c for c in await run_checks(keyed)}
    assert checks["brave"].ok
    assert checks["brave"].detail == "1 results"


@respx.mock
async def test_brave_answering_nothing_fails(settings):
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"brave_search_api_key": SecretStr("good")})
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    respx.get(BRAVE).mock(return_value=httpx.Response(200, json={"web": {}}))
    checks = {c.name: c for c in await run_checks(keyed)}
    assert checks["brave"].ok is False


@respx.mock
async def test_the_search_check_asks_searxng_alone(settings):
    """Through `search`, a working Brave would stand in for a broken SearXNG
    and the check would pass while the free provider is down."""
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"brave_search_api_key": SecretStr("good")})
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    respx.get(f"{SEARX}/search").mock(return_value=httpx.Response(503))
    respx.get(BRAVE).mock(
        return_value=httpx.Response(
            200,
            json={"web": {"results": [{"url": "https://www.example-register.ch/"}]}},
        )
    )
    checks = {c.name: c for c in await run_checks(keyed)}
    assert checks["search"].ok is False
    assert "503" in checks["search"].detail
