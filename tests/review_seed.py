"""A fictional run, written the way the child graph writes one, for the
review page's tests: one company to send, one held, one without a site."""

import json
import sqlite3
from pathlib import Path

from fictional_profile import INVITATION, SENDER

from company_reach.models import CompanyRecord, Contact, ContactAddress, Draft
from company_reach.tools.db import (
    connect,
    init_db,
    record_contact,
    record_draft,
    record_searches,
    record_site,
    upsert_companies,
)
from company_reach.tools.invitation import FRAME_VERSION, assemble, subject
from company_reach.tools.llm import Provenance
from company_reach.tools.search import Asked, Result

RUN = "r1"
SEND, HOLD, SKIP = "CHE000000046", "CHE123456788", "CHE111111118"
SURVEY = "https://survey.test/form"
SENTENCE = "Ich schreibe Ihnen, weil Ihr Betrieb Treppen aus Stahl fertigt."
PROV = Provenance(
    model="test-model",
    prompt="draft",
    prompt_version="4",
    reasoning_effort="low",
    prompt_tokens=1,
    completion_tokens=1,
    finish_reason="stop",
    seconds=0.1,
)


def _company(uid: str, name: str, form: str) -> CompanyRecord:
    return CompanyRecord(
        uid=uid,
        name=name,
        legal_form=form,
        municipality="3203",
        city="Musterstadt",
        purpose="Herstellung von Metallteilen.",
        purpose_head="Herstellung von Metallteilen.",
    )


def _result(conn: sqlite3.Connection, uid: str, rec: str, reason: str) -> None:
    conn.execute(
        "insert into results (run_id, uid, recommendation, reason, finished_at)"
        " values (?,?,?,?,'2026-09-24T10:00:00+00:00')",
        (RUN, uid, rec, reason),
    )


def seed(path: Path, *, link: str | None = None) -> None:
    """`link` overrides the survey link in the sendable draft, to test the
    placeholder gate."""
    init_db(path)
    with connect(path) as conn:
        upsert_companies(
            conn,
            [
                _company(SEND, "Muster Metallbau AG", "0106"),
                _company(HOLD, "Beispiel Holzbau GmbH", "0107"),
                _company(SKIP, "Exempel Druck AG", "0106"),
            ],
            "import",
        )
        for uid, batch in ((SKIP, 1), (HOLD, 1), (SEND, 1)):
            conn.execute(
                "insert into seen values (?,?,?,'2026-09-24T09:00:00+00:00')",
                (uid, RUN, batch),
            )

        # send: a site, a named contact, two addresses, a checked draft
        _result(
            conn, SEND, "send", "Anna Muster at the general inbox the site publishes"
        )
        record_site(
            conn,
            RUN,
            SEND,
            url="https://muster-metallbau.ch/",
            tier="uid",
            evidence="CHE-000.000.046",
            evidence_url="https://muster-metallbau.ch/impressum",
            queries=['"Muster Metallbau" Musterstadt'],
            candidates=["https://muster-metallbau.ch/"],
        )
        conn.execute(
            "insert into profiles values (?,?,?)",
            (RUN, SEND, json.dumps({"description": "Makes steel stair railings."})),
        )
        contact = Contact(
            name="Anna Muster",
            role="Inhaberin",
            email="info@muster-metallbau.ch",
            email_kind="generic",
            source="site",
            source_url="https://muster-metallbau.ch/team",
            alternatives=["Beat Beispiel, Mitglied des Verwaltungsrates"],
            addresses=[
                ContactAddress(email="info@muster-metallbau.ch", kind="generic"),
                ContactAddress(email="studio@agentur.example", kind="third_party"),
            ],
        )
        contact_id = record_contact(conn, RUN, SEND, contact)
        survey_link = link or f"{SURVEY}/?c={SEND}&l=de"
        body = assemble(
            contact, SENTENCE, link=survey_link, sender=SENDER, inv=INVITATION
        )
        record_draft(
            conn,
            RUN,
            SEND,
            Draft(
                subject=subject(contact, SENDER, INVITATION),
                body=body,
                model_text=SENTENCE,
                link=survey_link,
                mailto_fits=True,
                frame_version=FRAME_VERSION,
            ),
            contact_id=contact_id,
            provenance=PROV,
        )

        # hold: a site, an off-domain address only, no draft
        _result(
            conn, HOLD, "hold", "the only address, a@x.example, is on another domain"
        )
        record_site(
            conn,
            RUN,
            HOLD,
            url="javascript:alert(1)",  # a hostile URL must never become a link
            tier="model",
            evidence="Beispiel Holzbau",
            evidence_url="https://beispiel-holzbau.ch/",
            queries=[],
            candidates=[],
        )
        record_contact(
            conn,
            RUN,
            HOLD,
            Contact(
                name=None,
                email="a@x.example",
                email_kind="third_party",
                source="site",
                addresses=[ContactAddress(email="a@x.example", kind="third_party")],
            ),
        )

        # skip: no site, the searches tried
        _result(
            conn, SKIP, "skip", "no website found after 3 searches and a domain guess"
        )
        record_site(
            conn,
            RUN,
            SKIP,
            url=None,
            queries=['"Exempel Druck" Musterstadt', "Exempel Druck Impressum"],
            candidates=[],
        )
        record_searches(
            conn,
            RUN,
            SKIP,
            [
                Asked(
                    '"Exempel Druck" Musterstadt',
                    "searxng",
                    unresponsive=["brave", "duckduckgo"],
                ),
                Asked(
                    "Exempel Druck Impressum",
                    "searxng",
                    error="SearXNG answered HTTP 503",
                ),
                Asked(
                    "Exempel Druck Impressum",
                    "brave",
                    [
                        Result(
                            "https://www.moneyhouse.ch/de/company/exempel",
                            "t",
                            "s",
                            "brave-api",
                            provider="brave",
                        )
                    ],
                ),
            ],
        )
