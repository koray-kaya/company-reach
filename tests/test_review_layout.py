"""The card's layout (issue #49): it never grows wider than the window, and
no content is cut off.

On a 1470 px screen the card overflowed: the top bar carried the whole goal
on one unbreakable line, the page's single grid column grew to it, and the
mail and the Send button ended up outside the window. The design keeps one
company per screen; a pane whose content is longer than the screen scrolls
on its own instead of cutting it. What a browser draws is checked by hand
with screenshots; these tests pin the decisions that make it right.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fictional_profile import profile_text
from review_seed import RUN, SURVEY, seed

from company_reach.review.app import create_app

CSS = (
    Path(__file__).parent.parent / "src/company_reach/review/static/review.css"
).read_text()


def rule(selector: str) -> str:
    """The declarations of the first rule for exactly this selector."""
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector}"
    return m.group(1)


@pytest.fixture
def html(settings) -> str:
    seed(settings.db_path)
    settings.profile_path.write_text(
        profile_text(SURVEY, goal="A goal long enough to push a one-line bar wide.")
    )
    client = TestClient(create_app(settings), base_url="http://127.0.0.1")
    return client.get(f"/review/{RUN}/0").text


def bar(html: str) -> str:
    return html[html.index('<header class="bar">') : html.index("</header>")]


def test_the_top_bar_does_not_carry_the_goal(html):
    # the goal is the same on every card, and it was what widened the page
    assert "A goal long enough" not in bar(html)


def test_previous_and_next_are_in_the_top_bar(html):
    top = bar(html)
    assert 'id="prev"' in top
    assert 'id="next"' in top
    assert "Mail 1 of 1" in top


def test_the_page_grid_cannot_grow_wider_than_the_window():
    assert "grid-template-columns:minmax(0,1fr)" in rule(".app")


def test_the_progress_marks_shrink_with_the_bar():
    # a run of thirty companies draws thirty marks
    assert "min-width:0" in rule(".progress")
    assert "flex:1" in rule(".progress i")


@pytest.mark.parametrize("selector", [".facts", ".letter .body"])
def test_a_long_pane_scrolls_instead_of_cutting(selector):
    assert "overflow:auto" in rule(selector)
    assert "overflow:hidden" not in rule(selector)


def test_the_recipient_and_the_subject_are_read_in_full():
    assert "nowrap" not in rule(".letter header dd")


def test_the_mail_is_shown_as_its_paragraphs(html):
    # a blank line between paragraphs cost a whole line of height each; the
    # end of the mail fell below the pane on a 738 px high window
    body = html[
        html.index('<div class="body">') : html.index(
            "</div>", html.index('<div class="body">')
        )
    ]
    paragraphs = re.findall(r"<p>(.*?)</p>", body, re.S)
    assert len(paragraphs) == 7  # frame@1: routing line … closing
    assert paragraphs[1].startswith("Guten Tag")
    assert "\n\n" not in body
