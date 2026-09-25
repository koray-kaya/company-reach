"""The card page agrees with the front page about what waits (issue #54).

The front page counts the mails nobody has decided yet; the card page
walked through every company of the run and, after the last mail, on into
the companies the run did not recommend."""

import pytest
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import HOLD, RUN, SEND, SKIP, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.settings import Settings
from company_reach.tools.db import connect

SAME = {"Sec-Fetch-Site": "same-origin"}
SECOND = "CHE000000047"  # a second recommended company, without a draft


@pytest.fixture
def client(settings: Settings) -> TestClient:
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    with connect(settings.db_path) as conn:
        conn.execute(
            """insert into results (run_id, uid, recommendation, reason, finished_at)
               values (?, ?, 'send', 'Muster Zwei at the inbox',
                       '2026-09-24T10:00:00+00:00')""",
            (RUN, SECOND),
        )
        # drawn in a later batch, so it is the second mail
        conn.execute(
            "insert into seen values (?,?,2,'2026-09-24T09:00:00+00:00')",
            (SECOND, RUN),
        )
    app = create_app(settings.model_copy(update={"sending_approved": True}))
    return TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)


def bar(html: str) -> str:
    return html[html.index('<header class="bar">') : html.index("</header>")]


def skip(client: TestClient, uid: str, n: int):
    return client.post(
        f"/decide/{RUN}/{uid}?n={n}", data={"action": "skip:Not a fit"}, headers=SAME
    )


def test_a_mail_card_counts_the_mails_and_what_waits(client):
    top = bar(client.get(f"/review/{RUN}/0").text)
    assert "Mail 1 of 2" in top
    assert "2 waiting" in top


def test_a_card_the_run_did_not_recommend_says_so(client):
    top = bar(client.get(f"/review/{RUN}/2").text)
    assert "Not recommended 1 of 2" in top
    assert "Mail" not in top


def test_the_run_opens_at_the_first_mail_waiting(client):
    skip(client, SEND, 0)
    assert client.get(f"/review/{RUN}").headers["location"] == f"/review/{RUN}/1"


def test_a_decided_mail_moves_on_to_the_next_mail_waiting(client):
    r = skip(client, SEND, 0)
    assert r.headers["location"].startswith(f"/review/{RUN}/1?")


def test_the_last_mail_decided_goes_back_to_the_front_page(client):
    skip(client, SEND, 0)
    r = skip(client, SECOND, 1)
    assert r.headers["location"].startswith("/?done=")
    home = client.get(r.headers["location"]).text
    assert "Every mail of this run is decided" in home
    assert "waiting for you" not in home


def test_a_card_the_run_did_not_recommend_moves_on_to_the_next_card(client):
    # browsing the rest is allowed; deciding one does not jump back
    r = skip(client, HOLD, 2)
    assert r.headers["location"].startswith(f"/review/{RUN}/3?")
    r = skip(client, SKIP, 3)
    assert r.headers["location"].startswith("/?done=")


def test_a_decided_mail_offers_the_next_mail(client):
    skip(client, SEND, 0)
    html = client.get(f"/review/{RUN}/0").text
    assert f'href="/review/{RUN}/1">Next mail →</a>' in html
    skip(client, SECOND, 1)
    html = client.get(f"/review/{RUN}/0").text
    assert 'href="/">All mails decided · Home</a>' in html
