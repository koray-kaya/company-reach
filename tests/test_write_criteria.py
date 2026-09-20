import json

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.nodes.write_criteria import write_criteria

URL = "https://api.openai.com/v1/chat/completions"
runner = CliRunner()

CRITERIA = {
    "must": ["makes or repairs something"],
    "must_not": ["holding", "real estate"],
    "positive_signals": ["Herstellung", "Montage"],
}


def answer(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                    "finish_reason": "stop",
                }
            ],
            "model": "GLM-5.3-Flash",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )


@respx.mock
async def test_goal_reaches_the_prompt(settings):
    route = respx.post(URL).mock(return_value=answer(CRITERIA))
    criteria, prov = await write_criteria("make windows", settings=settings)
    assert criteria.must == ["makes or repairs something"]
    assert prov.prompt == "criteria"
    sent = json.loads(route.calls[0].request.content)
    assert "make windows" in sent["messages"][0]["content"]


@respx.mock
def test_cli_prints_the_three_lists(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    respx.post(URL).mock(return_value=answer(CRITERIA))
    result = runner.invoke(cli.app, ["criteria", "--goal", "make windows"])
    assert result.exit_code == 0, result.output
    assert "must_not" in result.output
    assert "Herstellung" in result.output


@respx.mock
def test_cli_falls_back_to_profile_toml(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    profile = tmp_path / "profile.toml"
    profile.write_text('goal = "from the profile file"\nabout_me = "x"\n')
    monkeypatch.setattr(cli, "PROFILE_PATH", profile)
    route = respx.post(URL).mock(return_value=answer(CRITERIA))
    result = runner.invoke(cli.app, ["criteria"])
    assert result.exit_code == 0, result.output
    sent = json.loads(route.calls[0].request.content)
    assert "from the profile file" in sent["messages"][0]["content"]
