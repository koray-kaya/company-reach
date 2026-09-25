import json
from pathlib import Path

import httpx
import respx
from search_fakes import as_outcome
from typer.testing import CliRunner

from company_reach import cli
from company_reach.errors import SearchError
from company_reach.manifest import manifest_path
from company_reach.models import CompanyRecord, RawPerson, Score, SelectionCriteria
from company_reach.nodes import find_site as find_site_node
from company_reach.profile import goal_hash
from company_reach.tools import llm
from company_reach.tools.db import (
    connect,
    init_db,
    record_decision,
    record_seen,
    store_criteria,
    upsert_companies,
    upsert_scores,
)
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
    # every import is screened now; the old option still works, and says so
    assert "--run-id is no longer needed" in r.output


def test_screen_over_an_empty_database_fails(settings, monkeypatch):
    """Audit: `screen` that matched nothing printed "kept 0, dropped 0" and
    exited 0, which reads as done."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    r = runner.invoke(cli.app, ["screen"])
    assert r.exit_code == 1
    assert "run `pool`" in r.output


# --- several municipalities and `status` (audit K17) -------------------------


def _binding(uid: str, name: str) -> dict:
    return {
        "uid": {"type": "literal", "value": uid},
        "name": {"type": "literal", "value": name},
        "lf": {"type": "literal", "value": "0106"},
        "desc": {"type": "literal", "value": "Herstellung von Fenstern."},
    }


def _municipalities(settings) -> dict[str, int]:
    with connect(settings.db_path) as conn:
        return {
            r[0]: r[1]
            for r in conn.execute(
                "select municipality, count(*) from companies group by municipality"
            )
        }


@respx.mock
def test_pool_accepts_several_municipalities(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    other = {
        "head": PAGE["head"],
        "results": {
            "bindings": [
                _binding("CHE000000011", "Beispiel Fenster AG"),
                _binding("CHE000000012", "Muster Glas GmbH"),
            ]
        },
    }

    def by_municipality(request):
        asked = request.content.decode()
        return httpx.Response(200, json=other if "3443" in asked else PAGE)

    respx.post(settings.lindas_url).mock(side_effect=by_municipality)

    r = runner.invoke(
        cli.app, ["pool", "--municipality", "3203", "--municipality", "3443"]
    )
    assert r.exit_code == 0, r.output
    assert "3 companies stored for municipality 3203" in r.output
    assert "2 companies stored for municipality 3443" in r.output
    assert _municipalities(settings) == {"3203": 3, "3443": 2}

    r = runner.invoke(cli.app, ["pool", "--municipality", "3203, 3443"])
    assert r.exit_code == 0, r.output
    assert "2 companies stored for municipality 3443" in r.output

    r = runner.invoke(cli.app, ["pool", "--municipality", "3203> ; drop"])
    assert r.exit_code != 0  # goes into the query's IRI: digits only


def test_status_counts_per_municipality(settings, monkeypatch):
    """Audit: nothing said which towns were pooled, scored, drawn out or
    waiting for review; steering a campaign took hand-written SQL."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    version, _ = llm.load_prompt("score")
    current = dict(
        goal_hash=goal_hash("make and sell"),
        prompt_version=version,
        model=settings.llm_model,
        criteria_hash=None,
    )

    def company(uid: str, town: str, name: str = "Muster Fenster AG"):
        return CompanyRecord(
            uid=uid,
            name=name,
            legal_form="0106",
            municipality=town,
            purpose="Herstellung von Fenstern.",
            purpose_head="Herstellung von Fenstern.",
        )

    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                company("CHE000000001", "3203"),  # drawn, a send card, undecided
                company("CHE000000002", "3203"),  # drawn, sent
                company("CHE000000003", "3203"),  # drawable
                company("CHE000000004", "3203", "Muster AG in Liquidation"),
                company("CHE000000005", "3203"),  # below the bar
                company("CHE000000006", "3203"),  # kept, not scored
                company("CHE000000011", "3443"),  # drawable
                company("CHE000000012", "3443"),  # scored under an old prompt
            ],
            "import",
        )
        upsert_scores(
            conn,
            [
                Score(uid=uid, score=n, reason="x")
                for uid, n in (
                    ("CHE000000001", 9),
                    ("CHE000000002", 8),
                    ("CHE000000003", 9),
                    ("CHE000000005", 5),
                    ("CHE000000011", 7),
                )
            ],
            **current,
        )
        upsert_scores(
            conn,
            [Score(uid="CHE000000012", score=9, reason="x")],
            **(current | {"prompt_version": "0"}),
        )
        record_seen(conn, ["CHE000000001", "CHE000000002"], run_id="r1", batch_no=1)
        for uid in ("CHE000000001", "CHE000000002"):
            conn.execute(
                "insert into results (run_id, uid, recommendation, finished_at)"
                " values ('r1', ?, 'send', '2026-09-25T00:00:00+00:00')",
                (uid,),
            )
        record_decision(conn, "CHE000000002", "sent", address="a@b.example")

    r = runner.invoke(cli.app, ["status", "--goal", "make and sell"])

    assert r.exit_code == 0, r.output

    def counts(town: str) -> list[int]:
        line = next(x for x in r.output.splitlines() if x.startswith(town))
        return [int(n) for n in line.split()[1:]]

    # pooled, kept, scored, drawable, drawn, sent, undecided
    assert counts("3203") == [6, 5, 4, 1, 2, 1, 1]
    assert counts("3443") == [2, 2, 1, 1, 0, 0, 0]


def _seed_scored_pool(
    settings, scores: dict[str, int], *, prompt_version: str | None = None
) -> None:
    init_db(settings.db_path)
    version = prompt_version or llm.load_prompt("score")[0]
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
            criteria_hash=None,  # scored before criteria were stored
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


def test_the_manifest_records_the_criteria(settings, monkeypatch):
    """Audit H10: a run's companies were ranked against these rules, and the
    file has to say which."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})
    rules = SelectionCriteria(must=["makes"], must_not=["holds"], positive_signals=[])
    with connect(settings.db_path) as conn:
        store_criteria(
            conn,
            goal_hash("make and sell"),
            rules,
            criteria_hash="c1",
            model="test-model",
            prompt_version="1",
            adopt_unlinked=True,
        )

    r = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r1"]
    )

    assert r.exit_code == 0, r.output
    m = json.loads(manifest_path("r1", settings=settings).read_text())
    assert m["criteria_hash"] == "c1"
    assert m["criteria"]["must"] == ["makes"]
    assert m["counts"]["results"] == 1  # the adopted score is drawn


def test_run_dry_on_an_empty_database_says_what_to_run(settings, monkeypatch):
    """A zero-row report would look like a working run that found nothing."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)

    r = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r1"]
    )

    assert r.exit_code != 0
    # `pool` screens what it stores, so `screen` is no longer a step to name
    assert "pool" in r.output and "score" in r.output


def _dry_run(run_id: str = "r1"):
    return runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", run_id]
    )


def test_a_prompt_bump_says_rescore(settings, monkeypatch):
    """Audit: after a score.md version bump the guard still passed, and the
    run said "pool exhausted" with exit 0 about a pool that only needed
    scoring again."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(
        settings, {"CHE000000001": 9, "CHE000000002": 8}, prompt_version="0"
    )

    r = _dry_run()

    assert r.exit_code == 2
    version, _ = llm.load_prompt("score")
    assert f"0 companies clear score >= 7 for score@{version} / test-model" in r.output
    assert "2 are scored under another prompt version (score@0)" in r.output
    assert "run `score`" in r.output
    assert "pool exhausted" not in r.output


def test_nothing_above_the_threshold_says_so(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 6, "CHE000000002": 5})

    r = _dry_run()

    assert r.exit_code == 2
    assert "0 companies clear score >= 7" in r.output
    assert "2 are scored, the best 6" in r.output
    assert "pool exhausted" not in r.output


def test_a_drawn_out_pool_says_it_is_exhausted(settings, monkeypatch):
    """Everything that clears the bar was drawn by earlier runs."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})
    with connect(settings.db_path) as conn:
        record_seen(conn, ["CHE000000001"], run_id="r1", batch_no=1)

    r = _dry_run("r2")

    assert r.exit_code == 2
    assert "(1) was drawn, decided or suppressed already" in r.output
    assert "the pool is exhausted" in r.output
    assert "pool another municipality" in r.output


def test_run_target_reaches_the_loop_and_the_resume_hint(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})
    captured: dict = {}

    async def stopped_at_the_cap(state, **kwargs):
        captured.update(state)
        return state | {"sendable_count": 1, "batches_drawn": 3}

    monkeypatch.setattr(cli, "run_graph", stopped_at_the_cap)
    r = runner.invoke(
        cli.app,
        ["run", "--dry", "--goal", "make and sell", "--run-id", "r1", "--target", "5"],
    )

    assert r.exit_code == 0, r.output
    assert captured["target"] == 5
    assert "--target 5" in r.output.splitlines()[0]
    assert "1 of 5 sendable" in r.output
    assert "batch cap" in r.output


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
    monkeypatch.setattr(find_site_node, "search_outcome", as_outcome(one_candidate))
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
    monkeypatch.setattr(find_site_node, "search_outcome", as_outcome(one_candidate))
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


def test_retry_no_site_redoes_the_skips(settings, monkeypatch):
    """`retry --no-site` also redoes the companies written off as having no
    website — after a run whose search turned out to have been throttled
    (#20). Other skips, and the companies a reviewer already decided about,
    stay as they are."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    rows = [
        ("CHE000000001", None, None, "search"),
        ("CHE000000002", "skip", "no website found after 3 searches", None),
        ("CHE000000003", "skip", "distributor only", None),
        ("CHE000000004", "send", "because", None),
        ("CHE000000005", "skip", "no website found after 3 searches", None),
    ]
    with connect(settings.db_path) as conn:
        for uid, rec, reason, kind in rows:
            conn.execute(
                "insert into results (run_id, uid, recommendation, reason,"
                " error_kind, finished_at)"
                " values ('r1', ?, ?, ?, ?, '2026-09-24T00:00:00+00:00')",
                (uid, rec, reason, kind),
            )
        conn.execute(
            "insert into ledger (uid, status, decided_at)"
            " values ('CHE000000005', 'skipped', '2026-09-24T01:00:00+00:00')"
        )
    child = _Sends()
    monkeypatch.setattr(cli, "build_child", lambda settings: child)

    async def search_works(state, *, settings):
        return {}

    monkeypatch.setattr("company_reach.graph.probe_search", search_works)

    r = runner.invoke(cli.app, ["retry", "r1", "--no-site"])
    assert r.exit_code == 0, r.output
    assert sorted(child.seen) == ["CHE000000001", "CHE000000002"]
    assert "2 retried" in r.output

    child.seen.clear()
    runner.invoke(cli.app, ["retry", "r1"])
    assert child.seen == []  # both are finished now; plain retry redoes errors only


def test_run_prints_how_many_brave_queries_it_made(settings, monkeypatch):
    """Brave is paid by the query: each run says how many it spent."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})

    async def spends_two(state, **kwargs):
        with connect(settings.db_path) as conn:
            for provider in ("searxng", "brave", "searxng", "brave"):
                conn.execute(
                    "insert into searches (run_id, uid, query, provider, at)"
                    " values ('r3', 'CHE000000001', 'q', ?, 't')",
                    (provider,),
                )
        return state | {"pool_exhausted": True}

    monkeypatch.setattr(cli, "run_graph", spends_two)
    r = runner.invoke(cli.app, ["run", "--goal", "make and sell", "--run-id", "r3"])
    assert r.exit_code == 0, r.output
    assert "2 Brave queries" in r.output
    manifest = json.loads(manifest_path("r3", settings=settings).read_text())
    assert manifest["counts"]["brave_queries"] == 2


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


def test_run_names_its_id_before_it_starts(settings, monkeypatch):
    """A run that is stopped halfway can be resumed only with its id, so the
    id is printed before anything else happens."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})
    r = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r7"]
    )
    assert r.exit_code == 0, r.output
    first = r.output.splitlines()[0]
    assert "r7" in first and "--run-id r7" in first
    assert "CHE000000001" in r.output  # the company line, as it finished


def test_the_resume_hint_repeats_the_options_given(settings, monkeypatch):
    """Final review of Phase A: following the hint after `run --dry --goal X`
    would have started a real run with the profile's goal."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})
    r = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r8"]
    )
    first = r.output.splitlines()[0]
    assert "--dry" in first and '--goal "make and sell"' in first


def test_a_leftover_unfinished_company_is_reported(settings, monkeypatch):
    """Final review of Phase A: the summary counted errors from this call's
    children only, so a company left unfinished by an earlier crash went
    unmentioned."""
    from company_reach.graph import draw_batch, initial_state

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})
    state = initial_state(
        run_id="r9",
        goal="make and sell",
        about_me="",
        municipality="",
        settings=settings,
    )
    draw_batch(state, settings=settings)  # drawn, then the process died

    async def finish_nothing(state, **kwargs):
        return state | {"pool_exhausted": True}

    monkeypatch.setattr(cli, "run_graph", finish_nothing)
    r = runner.invoke(cli.app, ["run", "--goal", "make and sell", "--run-id", "r9"])
    assert "1 errors" in r.output
    assert "retry r9" in r.output

    # after --dry the hint must not be `retry`, which runs the real child
    dry = runner.invoke(
        cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r9"]
    )
    assert "company-reach retry" not in dry.output
    assert "finish them with: company-reach run --run-id r9 --dry" in dry.output


# --- redraft (frame@1) --------------------------------------------------------

NEW_SURVEY = "https://survey.test/new-form"
_PROV = llm.Provenance(
    model="test-model",
    prompt="draft",
    prompt_version="4",
    reasoning_effort="low",
    prompt_tokens=1,
    completion_tokens=1,
    finish_reason="stop",
    seconds=0.1,
)


def _card(settings, uid):
    """The card as the page would show it, against today's profile."""
    from review_seed import RUN

    from company_reach.profile import load_profile
    from company_reach.review.cards import load_cards

    profile = load_profile(settings.profile_path)
    with connect(settings.db_path) as conn:
        cards = load_cards(
            conn,
            RUN,
            survey_url=profile.survey_url,
            sending_approved=True,
            profile=profile,
        )
    return next(c for c in cards if c.uid == uid)


def _bad_stored_sentence(settings, uid, text="Ihre Firma ist besonders wertvoll."):
    """A stored draft whose own sentence breaks the rules: only the model
    can write a new one."""
    with connect(settings.db_path) as conn:
        conn.execute("update drafts set model_text = ? where uid = ?", (text, uid))


def _redraft_setup(settings, monkeypatch, *, sentence=None):
    """The seeded run, then the survey moved: every draft links to the old
    one. Only the drafting prompt may be asked, and nothing may be fetched
    or searched (respx refuses every request)."""
    from fictional_profile import profile_text
    from review_seed import SENTENCE, seed

    from company_reach.models import DraftAnswer

    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(NEW_SURVEY))
    asked: list[str] = []

    async def only_draft(prompt_name, output_model, *, settings, **variables):
        asked.append(prompt_name)
        assert prompt_name == "draft", f"redraft asked {prompt_name!r}"
        answer = DraftAnswer(sentence=sentence or SENTENCE)
        return answer, _PROV

    monkeypatch.setattr(llm, "ask", only_draft)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return asked


@respx.mock
def test_redraft_makes_stale_cards_sendable(settings, monkeypatch):
    """Review focus 3: `survey_url` changed after drafts existed. `redraft`
    restores every undecided send card from SQLite — draft and check only,
    no search, no fetch — and no card offers the old link any more."""
    from review_seed import RUN, SEND, SURVEY

    from company_reach.tools.invitation import survey_link

    asked = _redraft_setup(settings, monkeypatch)
    assert "survey_url" in _card(settings, SEND).send_block

    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    # only the frame changed: rebuilt around the same sentence, no model call
    assert asked == []
    assert "1 redrafted · 1 sendable" in r.output

    card = _card(settings, SEND)
    assert card.send_block is None
    assert card.draft.link == survey_link(NEW_SURVEY, SEND)
    assert f"{SURVEY}/" not in card.draft.body


@respx.mock
def test_redraft_rebuilds_a_profile_change_without_a_model_call(settings, monkeypatch):
    from fictional_profile import profile_text
    from review_seed import RUN, SEND, SURVEY

    asked = _redraft_setup(settings, monkeypatch)
    moved = profile_text(SURVEY).replace("closes = 2026-10-30", "closes = 2026-11-13")
    settings.profile_path.write_text(
        moved.replace('supervisor = "Prof. Dr. Hans Vorbild"\n', "")
    )
    assert "profile" in _card(settings, SEND).send_block

    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    assert asked == []
    card = _card(settings, SEND)
    assert card.send_block is None
    assert "offen bis Freitag, 13. November" in card.draft.body
    assert "Betreut von" not in card.draft.body


@respx.mock
def test_redraft_follows_a_newer_contact(settings, monkeypatch):
    from fictional_profile import profile_text
    from review_seed import RUN, SEND, SURVEY

    from company_reach.models import Contact
    from company_reach.tools.db import record_contact

    asked = _redraft_setup(settings, monkeypatch)
    settings.profile_path.write_text(profile_text(SURVEY))
    with connect(settings.db_path) as conn:
        record_contact(
            conn,
            RUN,
            SEND,
            Contact(
                name="Beat Beispiel",
                role="Geschäftsführer",
                email="info@muster-metallbau.ch",
                email_kind="generic",
                source="site",
            ),
        )
    assert "contact" in _card(settings, SEND).send_block

    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    assert asked == []
    card = _card(settings, SEND)
    assert card.send_block is None
    assert "Guten Tag Beat Beispiel" in card.draft.body


@respx.mock
def test_redraft_asks_the_model_when_the_sentence_itself_fails(settings, monkeypatch):
    from review_seed import RUN, SEND

    asked = _redraft_setup(settings, monkeypatch)
    _bad_stored_sentence(settings, SEND)
    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    assert asked == ["draft"]
    assert _card(settings, SEND).send_block is None


@respx.mock
def test_redraft_leaves_a_decided_card_alone(settings, monkeypatch):
    from review_seed import RUN, SEND

    from company_reach.tools.db import record_decision

    asked = _redraft_setup(settings, monkeypatch)
    with connect(settings.db_path) as conn:
        record_decision(conn, SEND, "skipped", note="Not a fit")
    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    assert asked == []
    assert "nothing to redraft" in r.output


@respx.mock
def test_redraft_a_current_card_only_when_named(settings, monkeypatch):
    from fictional_profile import profile_text
    from review_seed import RUN, SEND, SURVEY

    asked = _redraft_setup(settings, monkeypatch)
    settings.profile_path.write_text(profile_text(SURVEY))  # nothing is stale
    assert runner.invoke(cli.app, ["redraft", RUN]).exit_code == 0
    assert asked == []
    r = runner.invoke(cli.app, ["redraft", RUN, "--uid", SEND])
    assert r.exit_code == 0, r.output
    assert asked == ["draft"]


@respx.mock
def test_redraft_that_fails_twice_holds_the_company(settings, monkeypatch):
    from review_seed import RUN, SEND

    asked = _redraft_setup(
        settings, monkeypatch, sentence="Ihre Firma ist besonders wertvoll."
    )
    _bad_stored_sentence(settings, SEND)
    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    assert asked == ["draft", "draft"]
    card = _card(settings, SEND)
    assert card.recommendation == "hold"
    assert card.draft is None
    assert "failed its checks twice" in card.reason


@respx.mock
def test_redraft_picks_up_a_hold_whose_draft_failed_twice(settings, monkeypatch):
    """A draft that failed twice was deleted and the company held; the
    cause may be gone (a profile fixed, a better model). `redraft` tries
    again, and a draft that passes makes it a send card again."""
    from fictional_profile import profile_text
    from review_seed import RUN, SEND, SURVEY

    asked = _redraft_setup(settings, monkeypatch)
    settings.profile_path.write_text(profile_text(SURVEY))
    with connect(settings.db_path) as conn:
        conn.execute("delete from drafts where uid = ?", (SEND,))
        conn.execute(
            "update results set recommendation = 'hold', reason = ? where uid = ?",
            ("the draft failed its checks twice: the sentence uses 'wertvoll'", SEND),
        )
    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 0, r.output
    assert asked == ["draft"]
    card = _card(settings, SEND)
    assert card.recommendation == "send"
    assert "general inbox" in card.reason
    assert card.send_block is None


def test_redraft_without_sender_asks_no_model(settings, monkeypatch):
    from fictional_profile import profile_text
    from review_seed import RUN

    asked = _redraft_setup(settings, monkeypatch)
    settings.profile_path.write_text(profile_text(NEW_SURVEY, sections=False))
    r = runner.invoke(cli.app, ["redraft", RUN])
    assert r.exit_code == 1
    assert "sender.name" in r.output
    assert asked == []
