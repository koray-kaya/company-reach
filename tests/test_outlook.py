"""Send through Outlook on the web (issue #52).

With `MAIL_CLIENT=outlook`, Send still records first; then the new tab it
was pressed into goes to Outlook's compose page with the draft filled in,
and the card's own tab shows the recorded send with "It was not sent"."""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import RUN, SEND, SKIP, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.settings import Settings
from company_reach.tools.db import connect
from company_reach.tools.mailto import outlook

SAME = {"Sec-Fetch-Site": "same-origin"}
COMPOSE = "https://outlook.office.com/mail/deeplink/compose"
INBOX = "info@muster-metallbau.ch"


def test_the_compose_link_carries_the_draft_exactly():
    link = outlook(COMPOSE, INBOX, "Für Frau Muster", "Guten Tag\n\nA & B = 100%")
    parts = urlsplit(link.href)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == COMPOSE
    query = parse_qs(parts.query)
    assert query["to"] == [INBOX]
    assert query["subject"] == ["Für Frau Muster"]
    assert query["body"] == ["Guten Tag\r\n\r\nA & B = 100%"]
    assert link.fits


def test_a_body_too_long_for_a_link_does_not_fit():
    assert not outlook(COMPOSE, INBOX, "s", "x" * 9000).fits


def test_the_setting_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("MAIL_CLIENT", "outlook")
    s = Settings(_env_file=None, llm_api_key="k", llm_model="m")
    assert s.mail_client == "outlook"
    assert s.outlook_compose_url == COMPOSE
    monkeypatch.delenv("MAIL_CLIENT")
    assert Settings(_env_file=None, llm_api_key="k", llm_model="m").mail_client == (
        "mailto"
    )


@pytest.fixture
def web(settings: Settings) -> Settings:
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    return settings.model_copy(
        update={"sending_approved": True, "mail_client": "outlook"}
    )


def client_for(settings: Settings) -> TestClient:
    return TestClient(
        create_app(settings), base_url="http://127.0.0.1", follow_redirects=False
    )


def shown(html: str) -> dict[str, str]:
    import re

    return dict(re.findall(r'name="(draft_id|body_sha256)" value="([^"]*)"', html))


def send(client: TestClient) -> "object":
    card = shown(client.get(f"/review/{RUN}/0").text)
    return client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": INBOX, **card},
        headers=SAME,
    )


def test_send_opens_in_a_new_tab(web):
    html = client_for(web).get(f"/review/{RUN}/0").text
    assert 'value="send" formtarget="_blank"' in html
    assert "Opens in Outlook in a new tab" in html


def test_the_mail_program_keeps_the_same_tab(settings):
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    html = (
        client_for(settings.model_copy(update={"sending_approved": True}))
        .get(f"/review/{RUN}/0")
        .text
    )
    assert "formtarget" not in html


def test_send_records_first_then_goes_to_outlook(web):
    r = send(client_for(web))
    assert r.status_code == 303
    target = urlsplit(r.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == COMPOSE
    query = parse_qs(target.query)
    assert query["to"] == [INBOX]
    assert query["body"][0].startswith("Zuhanden Frau Muster")
    with connect(web.db_path) as conn:
        [row] = conn.execute("select uid, status, address from ledger").fetchall()
    assert tuple(row) == (SEND, "sent", INBOX)


def test_a_mail_too_long_for_a_link_is_shown_to_copy(web, monkeypatch):
    from company_reach.tools import mailto

    monkeypatch.setattr(mailto, "OUTLOOK_LIMIT", 100)
    r = send(client_for(web))
    assert r.status_code == 200
    assert "Copy the text" in r.text
    assert "Open the draft in Outlook" in r.text


def test_the_card_offers_it_was_not_sent_right_after_send(web):
    client = client_for(web)
    send(client)
    html = client.get(f"/review/{RUN}/0").text
    assert "Recorded: sent" in html
    assert 'value="not_sent"' in html
    with connect(web.db_path) as conn:
        sent_id = conn.execute("select id from ledger").fetchone()[0]
    assert f'name="sent_id" value="{sent_id}"' in html
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "not_sent", "sent_id": str(sent_id), "confirm": "yes"},
        headers=SAME,
    )
    assert r.status_code == 303
    assert "Recorded: sent" not in client.get(f"/review/{RUN}/0").text


def test_it_was_not_sent_goes_once_something_else_is_decided(web):
    client = client_for(web)
    send(client)
    client.post(
        f"/decide/{RUN}/{SKIP}?n=2", data={"action": "skip:Not a fit"}, headers=SAME
    )
    assert 'value="not_sent"' not in client.get(f"/review/{RUN}/0").text
