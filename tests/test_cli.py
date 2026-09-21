import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.manifest import manifest_path
from company_reach.models import CompanyRecord, Score
from company_reach.nodes import find_site as find_site_node
from company_reach.profile import goal_hash
from company_reach.tools import llm
from company_reach.tools.db import connect, init_db, upsert_companies, upsert_scores
from company_reach.tools.search import Result

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


def _seed_scored_pool(settings, scores: dict[str, int]) -> None:
    init_db(settings.db_path)
    version, _ = llm.load_prompt("score")
    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                CompanyRecord(
                    uid=uid,
                    name=f"Firma {uid[-2:]} AG",
                    legal_form="0106",
                    municipality="3203",
                    purpose="Betrieb einer Schreinerei.",
                    purpose_head="Betrieb einer Schreinerei.",
                )
                for uid in scores
            ],
            "import",
        )
        upsert_scores(
            conn,
            [Score(uid=uid, score=n, reason="x") for uid, n in scores.items()],
            goal_hash=goal_hash("make and sell"),
            prompt_version=version,
            model=settings.llm_model,
        )


def test_run_dry_loops_over_the_stub_child(settings, monkeypatch):
    """No network and no model call: the child is the M3 stub, so the command
    demonstrates the loop before M4 exists."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9, "CHE000000002": 7})

    r = runner.invoke(
        cli.app,
        ["run", "--dry", "--goal", "make and sell", "--run-id", "r1"],
    )

    assert r.exit_code == 0, r.output
    assert "2 companies" in r.output
    assert "0 sendable" in r.output
    assert "manifest" in r.output


def test_run_dry_writes_a_finished_manifest(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})

    runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r1"]
    )

    m = json.loads(manifest_path("r1", settings=settings).read_text())
    assert m["status"] == "done"
    assert m["counts"]["results"] == 1
    assert m["goal"] == "make and sell"


def test_run_dry_on_an_empty_database_says_what_to_run(settings, monkeypatch):
    """A zero-row report would look like a working run that found nothing."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)

    r = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r1"]
    )

    assert r.exit_code != 0
    assert "pool" in r.output and "screen" in r.output and "score" in r.output


def _seed_one_company(settings) -> None:
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                CompanyRecord(
                    uid="CHE000000046",
                    name="Muster Metallbau AG",
                    legal_form="0106",
                    municipality="3203",
                    street="Beispielstrasse 1",
                    postal_code="8000",
                    city="Musterstadt",
                    purpose="Herstellung und Verkauf von Metallteilen.",
                    purpose_head="Herstellung und Verkauf von Metallteilen.",
                )
            ],
            "import",
        )


@respx.mock
def test_enrich_until_site_prints_the_site_and_its_tier(settings, monkeypatch):
    """The milestone's demo: one company, the site, the tier, the evidence."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_one_company(settings)

    async def resolve(host):
        return ["93.184.216.34"]

    async def no_guesses(names, **kw):
        return []

    async def one_candidate(query, *, settings, limit=10):
        return [
            Result("https://muster-metallbau.ch/", "Muster Metallbau AG", "x", "ddg")
        ]

    async def ask(prompt_name, output_model, *, settings, **variables):
        return output_model(
            chosen_url="https://muster-metallbau.ch/",
            quote="Muster Metallbau AG",
            reason="fits",
        ), None

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    monkeypatch.setattr(find_site_node, "resolving_domains", no_guesses)
    monkeypatch.setattr(find_site_node, "search", one_candidate)
    monkeypatch.setattr(find_site_node.llm, "ask", ask)

    html = (
        "<html><body><div id='footer'><p>Muster Metallbau AG<br>"
        "Beispielstrasse 1<br>8000 Musterstadt<br>CHE-000.000.046 MWST</p>"
        "</div></body></html>"
    )
    respx.get("https://muster-metallbau.ch/robots.txt").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://muster-metallbau.ch/").mock(
        return_value=httpx.Response(200, html=html)
    )
    respx.get("https://muster-metallbau.ch/impressum").mock(
        return_value=httpx.Response(200, html=html)
    )
    respx.get("https://muster-metallbau.ch/sitemap.xml").mock(
        return_value=httpx.Response(404)
    )
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))

    r = runner.invoke(cli.app, ["enrich", "--uid", "CHE000000046", "--until", "site"])
    assert r.exit_code == 0, r.output
    assert "muster-metallbau.ch" in r.output
    assert "uid" in r.output


def test_enrich_on_an_unknown_company_says_so(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    r = runner.invoke(cli.app, ["enrich", "--uid", "CHE000000046", "--until", "site"])
    assert r.exit_code != 0
    assert "CHE000000046" in r.output


def test_run_no_longer_refuses_without_dry(settings, monkeypatch):
    """M3's guard said only --dry exists until M4 gives the child its nodes.
    It has them now, so the guard has to go — a command that lies about what
    it can do is worse than one that does not exist."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    r = runner.invoke(cli.app, ["run", "--goal", "make and sell", "--run-id", "r1"])
    # It stops on the empty pool, not on the flag.
    assert "only --dry" not in r.output
