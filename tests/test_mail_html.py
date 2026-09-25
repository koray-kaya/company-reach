"""The mail as HTML, for the clipboard (issue #62).

Outlook on the web turns a URL into a link when it is typed or pasted, not
when a compose link fills the body in. So Send in Outlook also copies the
mail as HTML with the survey link as a real link, to paste over the
prefilled text."""

import re

from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import RUN, SURVEY, seed

from company_reach.review.app import create_app
from company_reach.tools.invitation import as_html

LINK = "https://survey.test/form/?c=CHE000000046&l=de"
BODY = (
    "Guten Tag Frau Muster\n\n"
    "Es geht um KMU & Lieferanten <klein>.\n\n"
    f"Zum Fragebogen (ohne Anmeldung):\n{LINK}\nDer Link enthält die UID."
)


def test_the_survey_link_becomes_a_real_link():
    html = as_html(BODY)
    href = LINK.replace("&", "&amp;")
    assert f'<a href="{href}">{href}</a>' in html


def test_text_is_escaped_and_paragraphs_stay_paragraphs():
    html = as_html(BODY)
    assert "KMU &amp; Lieferanten &lt;klein&gt;." in html
    assert html.count("<p>") == 3
    assert "(ohne Anmeldung):<br>" in html


def card(settings, mail_client):
    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    app = create_app(
        settings.model_copy(
            update={"sending_approved": True, "mail_client": mail_client}
        )
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    return client.get(f"/review/{RUN}/0").text


def test_an_outlook_card_carries_the_mail_to_copy(settings):
    html = card(settings, "outlook")
    template = re.search(r'<template id="mail-html">(.*?)</template>', html, re.S)
    assert template
    assert '<a href="https://survey.test/form/?c=CHE000000046&amp;l=de">' in (
        template.group(1)
    )
    assert "⌘A" in html and "⌘V" in html  # the page says what to do


def test_a_mail_program_card_does_not(settings):
    assert 'id="mail-html"' not in card(settings, "mailto")
