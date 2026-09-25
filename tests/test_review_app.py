"""The review page over HTTP: what it shows, and what it refuses to do.

`TestClient` sends no `Sec-Fetch-Site` header unless told to, which is what
a request from an old client or a script looks like; every POST here says
which kind of request it is on purpose.
"""

import re

import pytest
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import HOLD, RUN, SEND, SENTENCE, SKIP, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.settings import Settings
from company_reach.tools.db import connect, decision_for, is_suppressed

SAME = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def review(settings: Settings) -> Settings:
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
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
    settings.profile_path.write_text(profile_text(SURVEY))
    locked = TestClient(create_app(settings))  # sending_approved defaults to False
    assert "SENDING_APPROVED" in locked.get(f"/review/{RUN}/0").text


def test_an_unknown_run_is_404(client):
    assert client.get("/review/nope").status_code == 404


def test_a_position_past_the_end_is_404(client):
    assert client.get(f"/review/{RUN}/9").status_code == 404


# --- deciding ----------------------------------------------------------------


def shown(html: str) -> dict[str, str]:
    """The draft the card rendered into its form: Send names what it sends."""
    return dict(re.findall(r'name="(draft_id|body_sha256)" value="([^"]*)"', html))


def post_send(
    client: TestClient,
    to: str,
    *,
    uid: str = SEND,
    n: int = 0,
    card: dict[str, str] | None = None,
):
    """Send as the page does: from the card as it was opened, unless `card`
    says what an earlier view of it showed."""
    if card is None:
        card = shown(client.get(f"/review/{RUN}/{n}").text)
    return client.post(
        f"/decide/{RUN}/{uid}?n={n}",
        data={"action": "send", "to": to, **card},
        headers=SAME,
    )


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
    r = post_send(client, "info@muster-metallbau.ch")
    assert r.status_code == 200
    assert decision(review, SEND) == "sent"
    assert 'http-equiv="refresh"' in r.text
    assert "mailto:info@muster-metallbau.ch?subject=" in r.text
    with connect(review.db_path) as conn:
        row = conn.execute("select address, run_id, draft_id from ledger").fetchone()
    assert (row["address"], row["run_id"]) == ("info@muster-metallbau.ch", RUN)
    assert row["draft_id"] is not None


def test_a_profile_change_blocks_send_on_the_server(client, review):
    # the closing date moved after drafting: the mail would promise the old one
    text = profile_text(SURVEY).replace("closes = 2026-10-30", "closes = 2026-11-13")
    review.profile_path.write_text(text)
    assert "redraft" in client.get(f"/review/{RUN}/0").text
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "info@muster-metallbau.ch"},
        headers=SAME,
    )
    assert r.status_code == 409
    assert ledger_rows(review) == 0


def test_send_copies_frame_arm_and_contact_kind(client, review):
    post_send(client, "info@muster-metallbau.ch")
    with connect(review.db_path) as conn:
        row = conn.execute(
            "select frame_version, arm, contact_kind from ledger"
        ).fetchone()
    assert tuple(row) == ("frame@1", "voll", "generic/site/named")


def test_send_goes_only_to_an_address_the_card_offered(client, review):
    r = post_send(client, "someone@elsewhere.example")
    assert r.status_code == 400
    assert ledger_rows(review) == 0


def test_send_is_refused_on_the_server_when_the_gate_is_closed(settings):
    # a disabled button is a hint, not a control: the server checks again
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
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
    card = shown(client.get(f"/review/{RUN}/0").text)
    post_send(client, "info@muster-metallbau.ch", card=card)
    r = post_send(client, "info@muster-metallbau.ch", card=card)
    assert r.status_code == 409
    assert ledger_rows(review) == 1


def test_a_changed_draft_is_not_sent(client, review):
    """The reviewer approves the text on screen; meanwhile the draft is
    rebuilt in place, under the same id — the salutation toggle in another
    tab, a redraft in a terminal. Send must not mail the text nobody read."""
    before = shown(client.get(f"/review/{RUN}/0").text)
    assert set(before) == {"draft_id", "body_sha256"}
    client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "salutation:ohne"}, headers=SAME
    )
    after = shown(client.get(f"/review/{RUN}/0").text)
    assert after["draft_id"] == before["draft_id"]  # the id alone cannot tell

    r = post_send(client, "info@muster-metallbau.ch", card=before)
    assert r.status_code == 409
    assert "the draft changed; reload the card" in r.text
    r = post_send(client, "info@muster-metallbau.ch", card={})  # names none
    assert r.status_code == 409
    assert ledger_rows(review) == 0

    assert post_send(client, "info@muster-metallbau.ch", card=after).status_code == 200


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
    post_send(client, "info@muster-metallbau.ch")
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


# --- frame@1 on the card -----------------------------------------------------


@pytest.fixture
def fallback(settings: Settings, monkeypatch) -> Settings:
    """A contact whose greeting fell back to the full name ("Gründer" on a
    site proposes nothing), and a model that must not be asked."""

    async def no_model(*args, **kwargs):
        raise AssertionError("the salutation toggle asked the model")

    monkeypatch.setattr("company_reach.tools.llm.ask", no_model)
    seed(settings.db_path, role="Gründer")
    settings.profile_path.write_text(profile_text(SURVEY))
    return settings.model_copy(update={"sending_approved": True})


def stored_draft(settings: Settings):
    with connect(settings.db_path) as conn:
        draft = conn.execute(
            "select subject, body, problems from drafts where uid = ?", (SEND,)
        ).fetchone()
        salutation = conn.execute(
            "select salutation from contacts where uid = ?", (SEND,)
        ).fetchone()[0]
    return draft, salutation


def test_choosing_herr_reassembles_without_a_model_call(fallback):
    client = TestClient(create_app(fallback), follow_redirects=False)
    before = client.get(f"/review/{RUN}/0").text
    assert "Anrede prüfen" in before
    assert "Guten Tag Anna Muster" in before

    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "salutation:Herr"}, headers=SAME
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/review/{RUN}/0?")

    draft, salutation = stored_draft(fallback)
    assert salutation == "Herr"
    assert draft["subject"] == "Für Herrn Muster: Masterarbeit an der OST"
    assert draft["body"].startswith(
        "Zuhanden Herrn Muster – besten Dank fürs Weiterleiten\n\n"
        "Guten Tag Herr Muster\n\n"
    )
    assert SENTENCE in draft["body"]
    assert draft["problems"] == ""  # checked again, and it passed
    after = client.get(f"/review/{RUN}/0").text
    assert "Anrede prüfen" not in after
    assert "your choice" in after
    assert 'value="send" disabled' not in after
    with connect(fallback.db_path) as conn:
        origin = conn.execute(
            "select salutation_origin from contacts where uid = ?", (SEND,)
        ).fetchone()[0]
    assert origin == "reviewer"


def test_the_card_names_where_the_salutation_came_from(client):
    html = client.get(f"/review/{RUN}/0").text  # "Inhaberin" proposes Frau
    assert "from the role" in html
    assert "Anrede prüfen" in html


def test_the_salutation_toggle_rebuilds_the_mail(review, client):
    # "ohne" takes back the Frau a feminine role proposed
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "salutation:ohne"}, headers=SAME
    )
    assert r.status_code == 303
    draft, salutation = stored_draft(review)
    assert salutation == "ohne"
    assert "\n\nGuten Tag Anna Muster\n\n" in draft["body"]
    assert draft["subject"] == "Für Anna Muster: Masterarbeit an der OST"


def test_an_unknown_salutation_is_refused(client, review):
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "salutation:Dr"}, headers=SAME
    )
    assert r.status_code == 400


def test_a_decided_card_keeps_its_salutation(client, review):
    assert post_send(client, "info@muster-metallbau.ch").status_code == 200
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "salutation:Herr"}, headers=SAME
    )
    assert r.status_code == 409


def test_a_third_party_row_cannot_be_sent(client, review):
    html = client.get(f"/review/{RUN}/0").text
    assert 'value="studio@agentur.example" disabled' in html
    r = post_send(client, "studio@agentur.example", card=shown(html))
    assert r.status_code == 409
    assert "another domain" in r.text
    assert ledger_rows(review) == 0


def test_the_kind_hint_follows_each_row(client):
    html = client.get(f"/review/{RUN}/0").text
    # one hint per address row; the page shows the selected row's
    assert html.count('class="rowhint"') == 2
    assert "The inbox the site publishes." in html
    assert "On another domain than the verified site" in html


def test_the_card_shows_frame_and_arm(client):
    html = client.get(f"/review/{RUN}/0").text
    assert "frame@1 · voll" in html


# --- the address row the mail is written for --------------------------------

ANNA, BEAT = "anna@muster-metallbau.ch", "beat@muster-metallbau.ch"
INBOX = "info@muster-metallbau.ch"


@pytest.fixture
def two_seen(settings: Settings, monkeypatch) -> Settings:
    """Two people's own addresses on the site, and the general inbox. The
    mail is written for the first: Anna, at her own address."""
    from company_reach.models import Contact, ContactAddress

    async def no_model(*args, **kwargs):
        raise AssertionError("choosing an address asked the model")

    monkeypatch.setattr("company_reach.tools.llm.ask", no_model)
    seed(
        settings.db_path,
        contact=Contact(
            name="Anna Muster",
            role="Inhaberin",
            email=ANNA,
            email_kind="seen",
            source="site",
            alternatives=[f"Beat Beispiel, Leiter Verkauf, {BEAT}"],
            addresses=[
                ContactAddress(email=ANNA, kind="seen"),
                ContactAddress(email=BEAT, kind="seen"),
                ContactAddress(email=INBOX, kind="generic"),
            ],
        ),
    )
    settings.profile_path.write_text(profile_text(SURVEY))
    return settings.model_copy(update={"sending_approved": True})


def choose(client, email):
    return client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": f"address:{email}"}, headers=SAME
    )


def test_another_seen_row_rebuilds_the_mail_for_its_owner(two_seen):
    """Review: picking another row sent Anna's mail — her greeting, "Ihre
    Adresse" — to Beat's address. The mail is rebuilt for the row chosen,
    and when the row is another named person's own address, for them."""
    client = TestClient(create_app(two_seen), follow_redirects=False)
    assert f'value="address:{BEAT}"' in client.get(f"/review/{RUN}/0").text

    assert choose(client, BEAT).status_code == 303
    html = client.get(f"/review/{RUN}/0").text
    assert f"&lt;{BEAT}&gt;" in html  # the letter's header
    assert f'name="to" value="{BEAT}" checked' in html
    with connect(two_seen.db_path) as conn:
        body = conn.execute("select body from drafts").fetchone()[0]
    assert body.startswith("Guten Tag Beat Beispiel\n\n")  # his own address
    assert "Anna" not in body

    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "send", "to": BEAT}, headers=SAME
    )
    assert r.status_code == 200
    with connect(two_seen.db_path) as conn:
        row = conn.execute("select address, contact_kind from ledger").fetchone()
    assert tuple(row) == (BEAT, "seen/site/named")


def test_the_inbox_row_gets_the_inbox_frame(two_seen):
    client = TestClient(create_app(two_seen), follow_redirects=False)
    choose(client, INBOX)
    with connect(two_seen.db_path) as conn:
        body, subject = conn.execute("select body, subject from drafts").fetchone()
    assert body.startswith("Zuhanden Frau Muster – besten Dank fürs Weiterleiten")
    assert "Ihren Namen und diese Adresse habe ich von Ihrer Website" in body
    assert subject == "Für Frau Muster: Masterarbeit an der OST"
    client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "send", "to": INBOX}, headers=SAME
    )
    with connect(two_seen.db_path) as conn:
        kind = conn.execute("select contact_kind from ledger").fetchone()[0]
    assert kind == "generic/site/named"


def test_send_to_a_row_the_mail_was_not_written_for_is_refused(two_seen):
    client = TestClient(create_app(two_seen), follow_redirects=False)
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "send", "to": BEAT}, headers=SAME
    )
    assert r.status_code == 409
    assert ledger_rows(two_seen) == 0


def test_a_third_party_row_cannot_be_chosen(client, review):
    assert choose(client, "studio@agentur.example").status_code == 409


# --- the address, not only the company (audit: address-level suppression) ---


def test_an_address_already_mailed_is_refused(client, review):
    """A sister company on the same site, with the same inbox, was written
    to in an earlier run: one inbox, one invitation."""
    from company_reach.tools.db import record_decision

    with connect(review.db_path) as conn:
        record_decision(
            conn, "CHE999999999", "sent", address="Info@Muster-Metallbau.ch"
        )
    html = client.get(f"/review/{RUN}/0").text
    assert 'value="info@muster-metallbau.ch" disabled' in html
    assert "already written to" in html
    r = post_send(client, "info@muster-metallbau.ch")
    assert r.status_code == 409
    assert "already written to" in r.text
    assert ledger_rows(review) == 1  # the sister's row only


def test_a_suppressed_address_is_refused(client, review):
    """The inbox asked to be forgotten through another company's mail."""
    from company_reach.tools.db import suppress

    with connect(review.db_path) as conn:
        suppress(conn, "info@muster-metallbau.ch", reason="forgotten on request")
    html = client.get(f"/review/{RUN}/0").text
    assert 'value="info@muster-metallbau.ch" disabled' in html
    assert "never-again list" in html
    r = post_send(client, "info@muster-metallbau.ch")
    assert r.status_code == 409
    assert "never-again list" in r.text
    assert ledger_rows(review) == 0


# --- a mail nobody received is not a contact (open point 4) ------------------


def statuses(review: Settings, uid: str = SEND) -> list[tuple]:
    with connect(review.db_path) as conn:
        return [
            tuple(r)
            for r in conn.execute(
                "select status, reverses from ledger where uid = ? order by id", (uid,)
            )
        ]


def take_back(client: TestClient, sent_id: str, *, uid: str = SEND, n: int = 0):
    return client.post(
        f"/decide/{RUN}/{uid}?n={n}",
        data={"action": "not_sent", "sent_id": sent_id},
        headers=SAME,
    )


def test_not_sent_reopens_the_card(client, review):
    """The mail client never opened, or the reviewer closed the mail
    unsent: the recorded page takes the send back, and the card is open
    again — the same address included, since no mail left."""
    from company_reach.tools.db import was_contacted

    recorded = post_send(client, "info@muster-metallbau.ch")
    sent_id = re.search(r'name="sent_id" value="(\d+)"', recorded.text).group(1)
    assert 'value="not_sent"' in recorded.text

    r = take_back(client, sent_id)
    assert r.status_code == 303
    assert decision(review, SEND) is None
    assert statuses(review) == [("sent", None), ("not_sent", int(sent_id))]
    with connect(review.db_path) as conn:
        assert not was_contacted(conn, SEND)
    assert post_send(client, "info@muster-metallbau.ch").status_code == 200


def test_not_sent_only_before_the_reviewer_moves_on(client, review):
    """Taking a send back belongs to the moment after it: once anything
    else is decided, `sent` is final."""
    recorded = post_send(client, "info@muster-metallbau.ch")
    sent_id = re.search(r'name="sent_id" value="(\d+)"', recorded.text).group(1)
    client.post(
        f"/decide/{RUN}/{SKIP}?n=2", data={"action": "skip:Not a fit"}, headers=SAME
    )
    r = take_back(client, sent_id)
    assert r.status_code == 409
    assert decision(review, SEND) == "sent"


def test_bounced_allows_another_address(client, review):
    """The inbox bounced. Its address goes on the never-again list, the
    card opens again, and another address may be tried — never the one
    that bounced."""
    from company_reach.models import Contact, ContactAddress
    from company_reach.tools.db import record_contact, was_contacted

    own = "anna.muster@muster-metallbau.ch"
    with connect(review.db_path) as conn:
        record_contact(
            conn,
            RUN,
            SEND,
            Contact(
                name="Anna Muster",
                role="Inhaberin",
                email="info@muster-metallbau.ch",
                email_kind="generic",
                source="site",
                source_url="https://muster-metallbau.ch/team",
                addresses=[
                    ContactAddress(email="info@muster-metallbau.ch", kind="generic"),
                    ContactAddress(email=own, kind="seen"),
                ],
            ),
        )
    assert post_send(client, "info@muster-metallbau.ch").status_code == 200
    assert 'value="bounced"' in client.get(f"/review/{RUN}/0").text

    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "bounced"}, headers=SAME
    )
    assert r.status_code == 303
    assert decision(review, SEND) is None
    html = client.get(f"/review/{RUN}/0").text
    assert 'value="info@muster-metallbau.ch" disabled' in html
    assert "(bounced)" in html
    assert f'value="{own}" checked' in html

    assert post_send(client, "info@muster-metallbau.ch").status_code == 409
    assert post_send(client, own).status_code == 200
    assert [s for s, _ in statuses(review)] == ["sent", "bounced", "sent"]
    with connect(review.db_path) as conn:
        assert is_suppressed(conn, "info@muster-metallbau.ch")
        assert was_contacted(conn, SEND)


def test_only_a_sent_company_can_bounce(client, review):
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0", data={"action": "bounced"}, headers=SAME
    )
    assert r.status_code == 409
    assert ledger_rows(review) == 0
