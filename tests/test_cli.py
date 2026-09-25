import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.errors import SearchError
from company_reach.manifest import manifest_path
from company_reach.models import CompanyRecord, RawPerson, Score
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


@respx.mock
def test_enrich_until_profile_prints_the_checked_profile(settings, monkeypatch):
    """M5's demo command. The person printed is the one the served page
    names; a person the stub invents would be dropped before printing,
    which is the point of check_profile standing between them."""
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
        if prompt_name == "pick_site":
            answer = dict(
                chosen_url="https://muster-metallbau.ch/",
                quote="Muster Metallbau AG",
                reason="fits",
            )
        elif prompt_name == "pick_pages":
            answer = dict(urls=["https://muster-metallbau.ch/impressum"])
        else:
            answer = dict(
                description="Baut Metallteile in Musterstadt.",
                size_signal="seit 1974",
                persons=[
                    RawPerson(name="Anna Muster", role="GL"),
                    RawPerson(name="Klara Erfunden"),
                ],
                addresses=["Beispielstrasse 1, 8000 Musterstadt"],
            )
        return output_model(**answer), None

    monkeypatch.setattr("company_reach.tools.fetcher.resolve_host", resolve)
    monkeypatch.setattr(find_site_node, "resolving_domains", no_guesses)
    monkeypatch.setattr(find_site_node, "search", one_candidate)
    monkeypatch.setattr(find_site_node.llm, "ask", ask)

    html = (
        "<html><body><div id='footer'><p>Muster Metallbau AG<br>"
        "Beispielstrasse 1<br>8000 Musterstadt<br>CHE-000.000.046 MWST<br>"
        "seit 1974, Anna Muster</p></div></body></html>"
    )
    for path in ("/robots.txt", "/sitemap.xml"):
        respx.get(f"https://muster-metallbau.ch{path}").mock(
            return_value=httpx.Response(404)
        )
    for path in ("/", "/impressum"):
        respx.get(f"https://muster-metallbau.ch{path}").mock(
            return_value=httpx.Response(200, html=html)
        )
    respx.get(host="muster-metallbau.ch").mock(return_value=httpx.Response(404))

    r = runner.invoke(
        cli.app, ["enrich", "--uid", "CHE000000046", "--until", "profile"]
    )

    assert r.exit_code == 0, r.output
    assert "Baut Metallteile in Musterstadt." in r.output
    assert "Anna Muster" in r.output
    assert "seit 1974" in r.output
    assert "Klara Erfunden" not in r.output


def test_enrich_refuses_a_stopping_point_it_does_not_have(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    r = runner.invoke(cli.app, ["enrich", "--uid", "CHE000000046", "--until", "draft"])
    assert r.exit_code != 0


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


class _Sends:
    """A child that sends for everyone and remembers whom it saw."""

    def __init__(self):
        self.seen: list[str] = []

    async def ainvoke(self, state, config=None):
        self.seen.append(state["uid"])
        return {"recommendation": "send", "reason": "because"}


def test_retry_re_enriches_the_errors_of_a_run(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        for uid, kind in (("CHE000000001", "search"), ("CHE000000002", None)):
            conn.execute(
                "insert into results (run_id, uid, recommendation, error_kind,"
                " finished_at) values ('r1', ?, ?, ?, '2026-09-24T00:00:00+00:00')",
                (uid, None if kind else "send", kind),
            )
    child = _Sends()
    monkeypatch.setattr(cli, "build_child", lambda settings: child)

    async def search_works(state, *, settings):
        return {}

    monkeypatch.setattr("company_reach.graph.probe_search", search_works)

    r = runner.invoke(cli.app, ["retry", "r1"])
    assert r.exit_code == 0, r.output
    assert child.seen == ["CHE000000001"]
    assert "1 retried · 1 recovered · 0 still failing" in r.output


def test_retry_with_nothing_to_retry_says_so(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    monkeypatch.setattr(cli, "build_child", lambda settings: _Sends())
    r = runner.invoke(cli.app, ["retry", "r1"])
    assert r.exit_code == 0, r.output
    assert "nothing to retry" in r.output


def test_import_v0_reports_what_it_added(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    fixture = str(Path(__file__).parent / "fixtures/v0")
    r = runner.invoke(cli.app, ["import-v0", "--v0-dir", fixture])
    assert r.exit_code == 0, r.output
    assert "3 companies marked as seen · 1 skip recorded" in r.output
    r = runner.invoke(cli.app, ["import-v0", "--v0-dir", fixture])
    assert "0 companies marked as seen · 0 skips recorded" in r.output


def test_review_serves_on_the_loopback_interface_by_default(settings, monkeypatch):
    # outside Docker the page must not be reachable from the network
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    served: dict = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: served.update(kw))
    r = runner.invoke(cli.app, ["review"])
    assert r.exit_code == 0, r.output
    assert (served["host"], served["port"]) == ("127.0.0.1", 8000)


def test_forget_suppresses_and_names_what_is_left_to_do(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    r = runner.invoke(cli.app, ["forget", "someone@nowhere.example"])
    assert r.exit_code == 0, r.output
    assert "suppressed: someone@nowhere.example" in r.output


def test_purge_says_what_it_removed(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    r = runner.invoke(cli.app, ["purge", "--older-than", "365"])
    assert r.exit_code == 0, r.output
    assert "0 companies older than 365 days purged" in r.output


def test_a_failed_run_says_why(settings, monkeypatch):
    """Audit K1: `raise typer.Exit(1) from error` printed nothing at all."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})

    async def boom(*args, **kwargs):
        raise SearchError("SearXNG unreachable: connection refused")

    monkeypatch.setattr(cli, "run_graph", boom)
    r = runner.invoke(cli.app, ["run", "--goal", "make and sell", "--run-id", "r1"])

    assert r.exit_code == 1
    assert "SearXNG unreachable" in r.output
    manifest = json.loads(manifest_path("r1", settings=settings).read_text())
    assert "SearXNG unreachable" in manifest["reason"]


def test_run_with_a_goal_keeps_about_me(settings, monkeypatch):
    """Audit H9: --goal blanked about_me, and the drafts said nothing about
    who writes."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    settings.profile_path.write_text(
        'goal = "Firms that make things."\n'
        'about_me = "Eine Studentin der Beispiel-Hochschule."\n'
        'survey_url = "https://survey.example/form"\n'
    )
    _seed_scored_pool(settings, {"CHE000000001": 9})
    captured: dict = {}

    async def fake_run_graph(state, **kwargs):
        captured.update(state)
        return state | {"pool_exhausted": True}

    monkeypatch.setattr(cli, "run_graph", fake_run_graph)
    r = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r2"]
    )

    assert r.exit_code == 0, r.output
    assert captured["about_me"] == "Eine Studentin der Beispiel-Hochschule."
