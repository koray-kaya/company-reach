"""A round shows its stages (issue #60).

A round takes about twenty minutes; the front page showed a raw log that
stayed still for minutes. Each step of a company's graph now records, as it
starts, where the company is; the front page lists the drawn companies with
that step or how they ended."""

import sys
import time

import pytest
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import HOLD, RUN, SEND, SKIP, SURVEY, seed

from company_reach.graph import STAGES, build_child, staged
from company_reach.models import CompanyRecord
from company_reach.review.app import create_app
from company_reach.review.jobs import Jobs
from company_reach.settings import Settings
from company_reach.tools.db import (
    connect,
    init_db,
    record_stage,
    run_progress,
    upsert_companies,
)

SAME = {"Sec-Fetch-Site": "same-origin"}
LATE = "CHE000000047"  # drawn in a second batch, still being worked on


def stage_of(settings: Settings, uid: str, run_id: str = "r1") -> str | None:
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "select stage from progress where run_id = ? and uid = ?", (run_id, uid)
        ).fetchone()
    return row["stage"] if row else None


# --- recording ----------------------------------------------------------------


async def test_a_step_records_its_name_before_it_runs(settings):
    init_db(settings.db_path)
    during = []

    async def node(state):
        during.append(stage_of(settings, state["uid"]))
        return {"x": 1}

    wrapped = staged("find_site", node, settings)
    assert await wrapped({"run_id": "r1", "uid": SEND}) == {"x": 1}
    assert during == ["find_site"]


async def test_a_plain_function_step_is_recorded_too(settings):
    init_db(settings.db_path)
    wrapped = staged("recommend", lambda state: {"recommendation": "send"}, settings)
    assert await wrapped({"run_id": "r1", "uid": SEND}) == {"recommendation": "send"}
    assert stage_of(settings, SEND) == "recommend"


def test_a_later_step_replaces_the_earlier_one(settings):
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        record_stage(conn, "r1", SEND, "find_site")
        record_stage(conn, "r1", SEND, "read_pages")
        rows = conn.execute("select stage from progress").fetchall()
    assert [r["stage"] for r in rows] == ["read_pages"]


def test_every_step_of_the_child_has_words_for_the_page(settings):
    nodes = set(build_child(settings=settings).get_graph().nodes)
    assert nodes - {"__start__", "__end__"} <= set(STAGES)


# --- what the page shows -------------------------------------------------------


@pytest.fixture
def page(settings: Settings) -> Settings:
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                CompanyRecord(
                    uid=LATE,
                    name="Nachzug Fenster AG",
                    legal_form="0106",
                    municipality="3203",
                    purpose="Herstellung von Fenstern.",
                    purpose_head="Herstellung von Fenstern.",
                )
            ],
            "import",
        )
        conn.execute(
            "insert into seen values (?,?,2,'2026-09-24T09:30:00+00:00')", (LATE, RUN)
        )
        record_stage(conn, RUN, LATE, "read_pages")
    return settings


def test_the_run_lists_its_companies_newest_batch_first(page):
    with connect(page.db_path) as conn:
        rows = run_progress(conn, RUN)
    assert [r["uid"] for r in rows][0] == LATE
    assert {r["uid"] for r in rows} == {LATE, SEND, HOLD, SKIP}
    late = rows[0]
    assert (late["batch_no"], late["stage"], late["recommendation"]) == (
        2,
        "read_pages",
        None,
    )


def test_the_run_id_is_read_from_the_command_output(tmp_path):
    jobs = Jobs(
        tmp_path,
        command=[sys.executable, "-c", "print('run r9 — if it stops, run it again')"],
    )
    job = jobs.start("round")
    job.process.wait()
    assert job.run_id == "r9"
    retry = Jobs(tmp_path / "b", command=[sys.executable, "-c", "pass"])
    assert retry.start("retry", "r7").run_id == "r7"


def running_round(page: Settings) -> tuple[TestClient, Jobs]:
    code = f"import time; print('run {RUN} — if it stops', flush=True); time.sleep(30)"
    jobs = Jobs(page.data_dir, command=[sys.executable, "-c", code])
    client = TestClient(
        create_app(page, jobs=jobs), base_url="http://127.0.0.1", follow_redirects=False
    )
    client.post("/jobs/round", headers=SAME)
    end = time.monotonic() + 10
    while jobs.current.run_id is None and time.monotonic() < end:
        time.sleep(0.05)
    return client, jobs


def test_a_running_round_shows_each_company_and_its_step(page):
    client, jobs = running_round(page)
    try:
        html = client.get("/").text
    finally:
        jobs.current.process.kill()
        jobs.current.process.wait()
    assert f"run {RUN}" in html
    assert "1 of 10 mails ready" in html
    assert "batch 2" in html
    assert "Nachzug Fenster AG" in html
    assert "reading the website" in html
    assert "mail ready" in html  # the sendable company of batch 1
    assert "skipped" in html
    assert "<details" in html and "Command output" in html


def test_forget_removes_the_company_from_the_progress_too(page):
    from company_reach.forget import forget

    with connect(page.db_path) as conn:
        record_stage(conn, RUN, SKIP, "recommend")
    forget(page, SKIP)
    assert stage_of(page, SKIP, RUN) is None


def test_what_is_being_worked_on_comes_first_in_its_batch():
    from company_reach.review.app import _progress_rows

    def row(name, batch, stage=None, rec=None):
        return {
            "name": name,
            "batch_no": batch,
            "stage": stage,
            "recommendation": rec,
            "reason": None,
            "error_kind": None,
        }

    lines = _progress_rows(
        [
            row("Alpha AG", 1, rec="send"),
            row("Beta AG", 1, stage="read_pages"),
            row("Gamma AG", 2, rec="skip"),
            row("Delta AG", 1),
        ]
    )
    assert [line["name"] for line in lines] == [
        "Gamma AG",  # the newest batch first
        "Beta AG",  # at work
        "Delta AG",  # not started
        "Alpha AG",  # done
    ]
