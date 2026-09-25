"""The review page over HTTP: what it shows, and what it refuses to do.

`TestClient` sends no `Sec-Fetch-Site` header unless told to, which is what
a request from an old client or a script looks like; every POST here says
which kind of request it is on purpose.
"""

import pytest
from fastapi.testclient import TestClient
from review_seed import HOLD, RUN, SEND, SKIP, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.settings import Settings
from company_reach.tools.db import connect, decision_for, is_suppressed

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
    assert "Für Frau Muster: Masterarbeit an der OST" in html
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


# --- deciding ----------------------------------------------------------------


def decision(review: Settings, uid: str) -> str | None:
    with connect(review.db_path) as conn:
        row = decision_for(conn, uid)
    return row["status"] if row else None


def ledger_rows(review: Settings) -> int:
    with connect(review.db_path) as conn:
        return conn.execute("select count(*) from ledger").fetchone()[0]


@pytest.mark.parametrize("header", [None, "cross-site", "same-site"])
def test_a_post_from_anywhere_but_this_page_is_refused(client, review, header):
    # the reviewer clicks the company's own site from the card; that site
    # must not be able to mark the company sent or suppress it (audit:242)
    headers = {"Sec-Fetch-Site": header} if header else {}
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "info@muster-metallbau.ch"},
        headers=headers,
    )
    assert r.status_code == 403
    assert ledger_rows(review) == 0


def test_a_typed_url_counts_as_this_page(client, review):
    r = client.post(
        f"/decide/{RUN}/{SKIP}?n=2",
        data={"action": "skip:Not a fit"},
        headers={"Sec-Fetch-Site": "none"},
    )
    assert r.status_code == 303


def test_send_records_first_then_hands_over_the_mailto(client, review):
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "info@muster-metallbau.ch"},
        headers=SAME,
    )
    assert r.status_code == 200
    assert decision(review, SEND) == "sent"
    assert 'http-equiv="refresh"' in r.text
    assert "mailto:info@muster-metallbau.ch?subject=" in r.text
    with connect(review.db_path) as conn:
        row = conn.execute("select address, run_id, draft_id from ledger").fetchone()
    assert (row["address"], row["run_id"]) == ("info@muster-metallbau.ch", RUN)
    assert row["draft_id"] is not None


def test_send_goes_only_to_an_address_the_card_offered(client, review):
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "someone@elsewhere.example"},
        headers=SAME,
    )
    assert r.status_code == 400
    assert ledger_rows(review) == 0


def test_send_is_refused_on_the_server_when_the_gate_is_closed(settings):
    # a disabled button is a hint, not a control: the server checks again
    seed(settings.db_path)
    settings.profile_path.write_text(
        f'goal = "Firms that make things."\nsurvey_url = "{SURVEY}"\n'
    )
    locked = TestClient(create_app(settings), follow_redirects=False)
    r = locked.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "info@muster-metallbau.ch"},
        headers=SAME,
    )
    assert r.status_code == 409
    assert ledger_rows(settings) == 0


def test_a_held_company_cannot_be_sent_by_posting_anyway(client, review):
    r = client.post(
        f"/decide/{RUN}/{HOLD}?n=1",
        data={"action": "send", "to": "a@x.example"},
        headers=SAME,
    )
    assert r.status_code == 409
    assert ledger_rows(review) == 0


def test_a_company_is_sent_once(client, review):
    data = {"action": "send", "to": "info@muster-metallbau.ch"}
    client.post(f"/decide/{RUN}/{SEND}?n=0", data=data, headers=SAME)
    r = client.post(f"/decide/{RUN}/{SEND}?n=0", data=data, headers=SAME)
    assert r.status_code == 409
    assert ledger_rows(review) == 1


def test_skip_records_the_reason_and_moves_on(client, review):
    r = client.post(
        f"/decide/{RUN}/{SKIP}?n=2", data={"action": "skip:Not a fit"}, headers=SAME
    )
    assert r.status_code == 303
    assert decision(review, SKIP) == "skipped"
    with connect(review.db_path) as conn:
        assert conn.execute("select note from ledger").fetchone()[0] == "Not a fit"


def test_undo_takes_a_skip_back_with_a_new_row(client, review):
    client.post(
        f"/decide/{RUN}/{SKIP}?n=2", data={"action": "skip:Not a fit"}, headers=SAME
    )
    r = client.post(f"/decide/{RUN}/{SKIP}?n=2", data={"action": "undo"}, headers=SAME)
    assert r.status_code == 303
    assert decision(review, SKIP) is None
    assert ledger_rows(review) == 2


def test_undo_never_takes_back_a_send(client, review):
    client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "info@muster-metallbau.ch"},
        headers=SAME,
    )
    r = client.post(f"/decide/{RUN}/{SEND}?n=0", data={"action": "undo"}, headers=SAME)
    assert r.status_code == 409
    assert decision(review, SEND) == "sent"


def test_never_again_asks_first_when_the_script_did_not(client, review):
    r = client.post(f"/decide/{RUN}/{SKIP}?n=2", data={"action": "never"}, headers=SAME)
    assert r.status_code == 200
    assert "cannot be undone" in r.text
    assert ledger_rows(review) == 0


def test_never_again_confirmed_suppresses_for_good(client, review):
    r = client.post(
        f"/decide/{RUN}/{SKIP}?n=2",
        data={"action": "never", "confirm": "yes"},
        headers=SAME,
    )
    assert r.status_code == 303
    assert decision(review, SKIP) == "never"
    with connect(review.db_path) as conn:
        assert is_suppressed(conn, SKIP)


def test_an_unknown_action_is_refused(client, review):
    r = client.post(f"/decide/{RUN}/{SKIP}?n=2", data={"action": "maybe"}, headers=SAME)
    assert r.status_code == 400
    assert ledger_rows(review) == 0


def test_the_front_page_lists_the_runs_to_review(client):
    html = client.get("/").text
    assert f'href="/review/{RUN}"' in html
