import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli

PAGE = json.loads((Path(__file__).parent / "fixtures/lindas_page.json").read_text())
runner = CliRunner()


@respx.mock
def test_pool_then_screen(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    respx.post(settings.lindas_url).mock(return_value=httpx.Response(200, json=PAGE))

    r = runner.invoke(cli.app, ["pool", "--municipality", "3203", "--run-id", "r1"])
    assert r.exit_code == 0, r.output
    assert "3 companies" in r.output

    r = runner.invoke(cli.app, ["screen", "--run-id", "r1"])
    assert r.exit_code == 0, r.output
    assert "kept 2" in r.output and "dropped 1" in r.output
