"""The Contacts page: making a personal link, and the list of everyone
contacted with what the survey says. Every POST states its Sec-Fetch-Site,
as in test_review_app."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from pydantic import SecretStr

from company_reach.models import CompanyRecord
from company_reach.review.app import create_app
from company_reach.settings import Settings
from company_reach.tools.db import connect, record_decision, suppress, upsert_companies

SAME = {"Sec-Fetch-Site": "same-origin"}
SURVEY = "https://survey.test"
TAGS = f"{SURVEY}/api/admin/tags"
UID = "CHE000000046"
ANNA = {
    "person": "Anna Muster",
    "company": "Muster Stahlbau AG",
    "channel": "linkedin",
    "profile": "https://www.linkedin.com/in/anna-muster/",
}


@pytest.fixture
def site(settings: Settings) -> Settings:
    settings.profile_path.write_text(profile_text(SURVEY))
    return settings


def client_for(settings: Settings) -> TestClient:
    return TestClient(
        create_app(settings), base_url="http://127.0.0.1", follow_redirects=False
    )


@pytest.fixture
def client(site: Settings) -> TestClient:
    return client_for(site)


def codes(settings: Settings) -> list[str]:
    with connect(settings.db_path) as conn:
        return [r["code"] for r in conn.execute("select code from invites")]


def add_company(settings: Settings, uid=UID, name="Muster Stahlbau AG") -> None:
    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                CompanyRecord(
                    uid=uid,
                    name=name,
                    legal_form="0106",
                    municipality="3203",
                    purpose="Herstellung von Treppen.",
                    purpose_head="Herstellung von Treppen.",
                )
            ],
            "r1",
        )


def test_the_page_opens_empty_and_says_answers_need_the_password(client):
    r = client.get("/contacts")
    assert r.status_code == 200
    assert "Nobody contacted yet" in r.text
    assert "FORM_ADMIN_PASSWORD" in r.text


def test_a_link_from_another_site_is_refused(client, site):
    r = client.post("/contacts", data=ANNA, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert codes(site) == []


def test_a_link_is_made_recorded_and_shown_in_both_languages(client, site):
    r = client.post("/contacts", data=ANNA, headers=SAME)
    assert r.status_code == 303
    (code,) = codes(site)
    assert r.headers["location"] == f"/contacts?new={code}#new"
    page = client.get(f"/contacts?new={code}").text
    assert f"{SURVEY}/?c={code}&amp;l=de" in page
    assert f"{SURVEY}/?c={code}&amp;l=en" in page
    assert "Anna Muster" in page


def test_a_company_the_tool_mailed_is_asked_about_before_anything_is_recorded(
    client, site
):
    with connect(site.db_path) as conn:
        record_decision(
            conn,
            UID,
            "sent",
            address="info@muster.example",
            decided_at="2026-09-20T09:00:00+00:00",
        )
    r = client.post("/contacts", data=ANNA | {"uid": UID}, headers=SAME)
    assert r.status_code == 200
    assert "The tool mailed this company on 2026-09-20" in r.text
    assert "Make the link anyway" in r.text
    assert codes(site) == []
    again = client.post(
        "/contacts", data=ANNA | {"uid": UID, "confirm": "yes"}, headers=SAME
    )
    assert again.status_code == 303
    assert len(codes(site)) == 1


def test_a_company_on_the_never_again_list_gets_no_link_even_confirmed(client, site):
    with connect(site.db_path) as conn:
        suppress(conn, UID, reason="never")
    r = client.post(
        "/contacts", data=ANNA | {"uid": UID, "confirm": "yes"}, headers=SAME
    )
    assert r.status_code == 400
    assert "never-again" in r.text
    assert codes(site) == []


def test_without_a_real_survey_address_no_link_is_made(settings):
    settings.profile_path.write_text(profile_text("https://survey.example/form"))
    client = client_for(settings)
    assert "still the example" in client.get("/contacts").text
    r = client.post("/contacts", data=ANNA, headers=SAME)
    assert r.status_code == 400
    assert codes(settings) == []


@respx.mock
def test_the_page_shows_who_answered(site):
    armed = site.model_copy(update={"form_admin_password": SecretStr("x" * 12)})
    client = client_for(armed)
    client.post("/contacts", data=ANNA, headers=SAME)
    (code,) = codes(armed)
    respx.get(TAGS).mock(
        return_value=httpx.Response(
            200,
            json={
                "tags": [
                    {
                        "tag": code,
                        "started_at": "2026-10-03T10:00:00+00:00",
                        "completed_at": "2026-10-03T10:12:00+00:00",
                    }
                ]
            },
        )
    )
    page = client.get("/contacts").text
    assert "1 completed" in page
    assert 'class="answer completed"' in page


@respx.mock
def test_a_survey_that_is_down_leaves_the_last_answers(site):
    armed = site.model_copy(update={"form_admin_password": SecretStr("x" * 12)})
    client = client_for(armed)
    client.post("/contacts", data=ANNA, headers=SAME)
    (code,) = codes(armed)
    respx.get(TAGS).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "tags": [
                        {
                            "tag": code,
                            "started_at": "2026-10-03T10:00:00+00:00",
                            "completed_at": None,
                        }
                    ]
                },
            ),
            httpx.ConnectError("down"),
        ]
    )
    assert "1 started" in client.get("/contacts").text
    page = client.get("/contacts").text
    assert r"Could not reach the survey" in page
    assert "1 started" in page


def test_an_unanswered_link_can_be_removed(client, site):
    client.post("/contacts", data=ANNA, headers=SAME)
    (code,) = codes(site)
    r = client.post(f"/contacts/{code}/remove", headers=SAME)
    assert r.status_code == 303
    assert codes(site) == []


def test_an_answered_link_stays(client, site):
    client.post("/contacts", data=ANNA, headers=SAME)
    (code,) = codes(site)
    with connect(site.db_path) as conn:
        conn.execute(
            "insert into responses (uid, started_at, imported_at) values (?, 'x', 'x')",
            (code,),
        )
    assert client.post(f"/contacts/{code}/remove", headers=SAME).status_code == 409
    assert codes(site) == [code]


def test_the_new_link_box_shows_the_matched_register_number(client, site):
    """Review Important 3b: whether the never-again list and the tool's
    mails were actually checked must be visible, not silent."""
    add_company(site)
    client.post("/contacts", data=ANNA, headers=SAME)
    (code,) = codes(site)
    page = client.get(f"/contacts?new={code}").text
    assert "Matched to CHE-000.000.046" in page


def test_the_new_link_box_says_when_the_company_did_not_match(client, site):
    client.post("/contacts", data=ANNA, headers=SAME)
    (code,) = codes(site)
    page = client.get(f"/contacts?new={code}").text
    assert (
        "Not found in the company pool: the never-again list and the tool's"
        " mails were checked by name only" in page
    )


def test_the_company_field_hints_at_why_to_pick_from_the_list(client):
    page = client.get("/contacts").text
    assert (
        "Pick a name from the list to check the never-again list and earlier"
        " mails." in page
    )


def test_the_log_marks_a_personal_link_that_did_not_match_the_register(client, site):
    client.post("/contacts", data=ANNA, headers=SAME)
    page = client.get("/contacts").text
    assert "not in pool" in page


def test_the_log_does_not_mark_a_matched_personal_link(client, site):
    add_company(site)
    client.post("/contacts", data=ANNA, headers=SAME)
    page = client.get("/contacts").text
    assert "not in pool" not in page


def test_the_front_page_leads_to_contacts(client):
    assert 'href="/contacts"' in client.get("/").text
