"""The review page over HTTP: what it shows, and what it refuses to do.

`TestClient` sends no `Sec-Fetch-Site` header unless told to, which is what
a request from an old client or a script looks like; every POST here says
which kind of request it is on purpose.
"""

import pytest
from fastapi.testclient import TestClient
from review_seed import RUN, SKIP, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.settings import Settings
from company_reach.tools.db import connect

SAME = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def review(settings: Settings) -> Settings:
    seed(settings.db_path)
    settings.profile_path.write_text(
        f'goal = "Firms that make things."\nsurvey_url = "{SURVEY}"\n'
    )
    return settings.model_copy(update={"sending_approved": True})


@pytest.fixture
def client(review: Settings) -> TestClient:
    return TestClient(create_app(review), follow_redirects=False)


# --- reading -----------------------------------------------------------------


def test_the_run_opens_at_the_first_undecided_company(client):
    r = client.get(f"/review/{RUN}")
    assert r.status_code == 303
    assert r.headers["location"] == f"/review/{RUN}/0"


def test_a_card_shows_the_decision_the_facts_and_the_letter(client):
    html = client.get(f"/review/{RUN}/0").text
    assert "Muster Metallbau AG" in html
    assert "Anna Muster at the general inbox the site publishes" in html
    assert "UID matches the register" in html
    assert "info@muster-metallbau.ch" in html
    assert "Umfrage zu meiner Masterarbeit" in html
    assert "1 / 3" in html
    assert 'name="to" value="info@muster-metallbau.ch" checked' in html


def test_a_held_company_offers_no_send(client):
    html = client.get(f"/review/{RUN}/1").text
    assert "Beispiel Holzbau GmbH" in html
    assert "No draft to send" in html
    assert 'value="send" disabled' in html


def test_a_hostile_site_url_is_not_a_link(client):
    html = client.get(f"/review/{RUN}/1").text
    assert "javascript:" not in html


def test_a_company_without_a_site_lists_the_searches(client):
    html = client.get(f"/review/{RUN}/2").text
    assert "Exempel Druck Impressum" in html
    assert "No site" in html


def test_page_text_is_escaped(client, review):
    with connect(review.db_path) as conn:
        conn.execute(
            "update companies set name = '<script>alert(1)</script>' where uid = ?",
            (SKIP,),
        )
    html = client.get(f"/review/{RUN}/2").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_locked_gate_is_explained(settings):
    seed(settings.db_path)
    settings.profile_path.write_text(
        f'goal = "Firms that make things."\nsurvey_url = "{SURVEY}"\n'
    )
    locked = TestClient(create_app(settings))  # sending_approved defaults to False
    assert "SENDING_APPROVED" in locked.get(f"/review/{RUN}/0").text


def test_an_unknown_run_is_404(client):
    assert client.get("/review/nope").status_code == 404


def test_a_position_past_the_end_is_404(client):
    assert client.get(f"/review/{RUN}/9").status_code == 404
