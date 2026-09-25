"""The front page (issue #44): where the campaign stands, and buttons that
start the everyday commands in the background, so a round needs no terminal.

The commands are replaced by a stand-in for `python -m company_reach`; what
each button runs is pinned in test_jobs.py.
"""

import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import RUN, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.review.jobs import Jobs
from company_reach.settings import Settings
from company_reach.tools.db import connect

SAME = {"Sec-Fetch-Site": "same-origin"}
LOCAL = "http://127.0.0.1:8000"


@pytest.fixture
def page(settings: Settings) -> Settings:
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    return settings


def open_page(settings: Settings, code: str = "pass") -> tuple[TestClient, Jobs]:
    jobs = Jobs(settings.data_dir, command=[sys.executable, "-c", code])
    return (
        TestClient(
            create_app(settings, jobs=jobs), base_url=LOCAL, follow_redirects=False
        ),
        jobs,
    )


def wait(jobs: Jobs, seconds: float = 20) -> None:
    end = time.monotonic() + seconds
    while jobs.current.running and time.monotonic() < end:
        time.sleep(0.05)
    assert not jobs.current.running


def stop(jobs: Jobs) -> None:
    if jobs.current is not None and jobs.current.running:
        jobs.current.process.kill()
        jobs.current.process.wait()


# --- what it shows -----------------------------------------------------------


def test_the_front_page_shows_where_the_campaign_stands(page):
    client, _ = open_page(page)
    html = client.get("/").text
    assert "<td>3203</td>" in html
    for column in ("pooled", "kept", "scored", "drawable", "drawn", "sent"):
        assert f"<th>{column}</th>" in html


def test_the_front_page_says_whether_send_is_locked(page):
    locked, _ = open_page(page)
    assert "Send is locked" in locked.get("/").text
    assert "SENDING_APPROVED" in locked.get("/").text
    opened, _ = open_page(page.model_copy(update={"sending_approved": True}))
    assert "Send is open" in opened.get("/").text


def test_the_front_page_offers_the_everyday_buttons(page):
    client, _ = open_page(page)
    html = client.get("/").text
    for action in ("round", "score", "pool", "doctor"):
        assert f'action="/jobs/{action}"' in html
    assert f'href="/review/{RUN}"' in html  # the runs are still listed
    assert 'action="/jobs/redraft"' in html


def test_retry_is_offered_only_for_a_run_with_errors(page):
    client, _ = open_page(page)
    assert 'action="/jobs/retry"' not in client.get("/").text
    with connect(page.db_path) as conn:
        conn.execute(
            """insert into results (run_id, uid, error_kind, error_text,
               finished_at) values ('r2', 'CHE000000047', 'fetch', 'timed out',
               '2026-09-25T10:00:00+00:00')"""
        )
    html = client.get("/").text
    assert 'action="/jobs/retry"' in html
    assert 'value="r2"' in html


def test_a_page_without_a_profile_still_opens(settings):
    # the first start: nothing pooled, no profile yet
    settings.profile_path.unlink(missing_ok=True)
    client, _ = open_page(settings)
    r = client.get("/")
    assert r.status_code == 200
    assert "profile.toml" in r.text


# --- what a button does ------------------------------------------------------


def test_a_button_starts_its_command_and_the_page_shows_the_output(page):
    client, jobs = open_page(page, "import sys; print('ran', sys.argv[1:])")
    r = client.post("/jobs/round", headers=SAME)
    assert r.status_code == 303
    assert r.headers["location"] == "/#job"
    wait(jobs)
    html = client.get("/").text
    assert "company-reach run --target 10" in html
    assert "ran [&#39;run&#39;, &#39;--target&#39;, &#39;10&#39;]" in html
    assert "finished" in html
    assert 'http-equiv="refresh"' not in html


def test_the_page_refreshes_while_a_command_runs(page):
    client, jobs = open_page(page, "import time; time.sleep(30)")
    client.post("/jobs/score", headers=SAME)
    try:
        html = client.get("/").text
        assert 'http-equiv="refresh"' in html
        assert "running" in html
    finally:
        stop(jobs)


def test_a_failed_command_is_shown_as_failed(page):
    client, jobs = open_page(page, "import sys; sys.exit('no model answered')")
    client.post("/jobs/doctor", headers=SAME)
    wait(jobs)
    html = client.get("/").text
    assert "failed" in html
    assert "no model answered" in html


def test_the_output_is_escaped(page):
    # a company's name or a page's text can end up in the output
    client, jobs = open_page(page, "print('<script>alert(1)</script>')")
    client.post("/jobs/doctor", headers=SAME)
    wait(jobs)
    html = client.get("/").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html


def test_pool_takes_the_numbers_from_the_form(page):
    client, jobs = open_page(page, "import sys; print(sys.argv[1:])")
    r = client.post("/jobs/pool", data={"value": "3203, 3443"}, headers=SAME)
    assert r.status_code == 303
    assert jobs.current.argv == ["pool", "--municipality", "3203,3443"]
    wait(jobs)


# --- what it refuses ---------------------------------------------------------


@pytest.mark.parametrize("header", [{}, {"Sec-Fetch-Site": "cross-site"}])
def test_a_button_pressed_anywhere_but_this_page_is_refused(page, header):
    # the company's own site is one click away on a card; it must not be able
    # to start anything here
    client, jobs = open_page(page)
    assert client.post("/jobs/round", headers=header).status_code == 403
    assert jobs.current is None


def test_a_bad_value_is_refused(page):
    client, jobs = open_page(page)
    r = client.post("/jobs/pool", data={"value": "3203 --run-id x"}, headers=SAME)
    assert r.status_code == 400
    assert client.post("/jobs/forget", headers=SAME).status_code == 400
    assert jobs.current is None


def test_a_second_button_while_one_runs_is_refused(page):
    client, jobs = open_page(page, "import time; time.sleep(30)")
    client.post("/jobs/score", headers=SAME)
    try:
        r = client.post("/jobs/round", headers=SAME)
        assert r.status_code == 409
        assert "score" in r.text
    finally:
        stop(jobs)


def test_a_command_from_before_a_restart_is_shown_and_blocks(page):
    client, jobs = open_page(page, "import time; time.sleep(30)")
    client.post("/jobs/round", headers=SAME)
    try:
        restarted, _ = open_page(page)
        html = restarted.get("/").text
        assert 'http-equiv="refresh"' in html
        assert "company-reach run --target 10" in html
        assert restarted.post("/jobs/score", headers=SAME).status_code == 409
    finally:
        stop(jobs)


def test_the_log_box_shows_the_newest_lines():
    # a box shorter than the tail, rebuilt by each refresh at its top, hid the
    # newest output of a long round
    css = (
        Path(__file__).parent.parent / "src/company_reach/review/static/review.css"
    ).read_text()
    rule = re.search(r"\.home \.log\{([^}]*)\}", css).group(1)
    assert "max-height" not in rule


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000"])
def test_a_request_for_another_host_is_refused(page, host):
    # DNS rebinding: a site whose name turns into 127.0.0.1 is "same origin"
    # to the browser; only the Host header tells it apart
    client, jobs = open_page(page)
    assert client.get("/", headers={"host": host}).status_code == 400
    r = client.post("/jobs/round", headers=SAME | {"host": host})
    assert r.status_code == 400
    assert jobs.current is None
    assert client.get("/", headers={"host": "localhost:8000"}).status_code == 200
