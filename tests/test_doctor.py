import json

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.tools.doctor import run_checks

URL = "https://api.openai.com/v1/chat/completions"
runner = CliRunner()


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
    checks = await run_checks(settings)
    assert [c.name for c in checks] == [
        "settings",
        "prompts",
        "database",
        "profile",
        "endpoint",
        "token budget",
    ]
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


@respx.mock
async def test_a_failing_check_does_not_stop_the_others(settings):
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    checks = await run_checks(settings)
    assert len(checks) == 6  # every check still ran
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
    assert len(checks) == 6
    assert not next(c for c in checks if c.name == "profile").ok
