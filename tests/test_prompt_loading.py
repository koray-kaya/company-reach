import re

import pytest

from company_reach.errors import PromptError
from company_reach.tools.llm import load_prompt, render


def test_header_is_parsed_and_stripped():
    version, text = load_prompt("score")
    assert version == "1"
    assert not text.startswith("---")
    assert "$goal" in text


def test_unknown_prompt_name_raises():
    with pytest.raises(PromptError, match="no-such-prompt"):
        load_prompt("no-such-prompt")


def test_render_substitutes_variables():
    version, text = render("criteria", goal="make things")
    assert version == "1"
    assert "make things" in text
    assert "$goal" not in text


def test_missing_variable_raises():
    with pytest.raises(PromptError, match="goal"):
        render("criteria")


def test_dollar_sign_in_data_is_left_alone():
    """Company text is data: a $word inside it must not be substituted away."""
    _, text = render(
        "score",
        goal="g",
        criteria="c",
        companies='[{"purpose": "Handel mit $Waren und $goal"}]',
    )
    assert "$Waren" in text
    assert "$goal" not in text.split("COMPANIES")[0]  # the real slot was filled
    assert "Handel mit $Waren und $goal" in text  # the data block is untouched


def test_a_prompt_variable_may_be_called_name():
    """`render(name, /, ...)`: without the positional-only marker the
    caller's name= collides with the parameter that says which prompt to
    load, and pick_site.md — whose first field is the company's name —
    cannot be rendered at all. Found by running the real thing; every test
    that mocks llm.ask is blind to it."""
    version, text = render(
        "pick_site",
        name="Muster AG",
        address="a",
        seat="s",
        uid="CHE000000046",
        candidates="c",
    )
    assert "Muster AG" in text
    assert version


def test_a_prompt_variable_may_be_called_prompt_name():
    with pytest.raises(PromptError):
        render("pick_site", prompt_name="x")


def test_the_prompts_ship_inside_the_package():
    # found planning M8: they sat three folders above llm.py, which works
    # from a checkout and fails for anyone who installs the package
    from importlib.resources import files

    shipped = {p.name for p in files("company_reach").joinpath("prompts").iterdir()}
    assert {"extract.md", "draft.md", "score.md", "pick_site.md"} <= shipped


def test_draft_v5_renders_with_the_eight_variables():
    from company_reach.tools.untrusted import as_data

    version, text = render(
        "draft",
        about_me="A thesis on how small firms find customers.",
        company_name="Muster Metallbau AG",
        seat="Musterstadt",
        role=as_data("Inhaberin", label="ROLE"),
        profile=as_data("Makes railings.", label="PROFILE"),
        feedback="",
        minutes="20",
        topic=as_data("wie KMU Personal finden", label="TOPIC"),
    )
    assert version == "5"
    assert "$" not in text
    assert "Ich schreibe Ihnen, weil" in text
    # the role is a block of its own, like the profile: page data, never
    # part of our sentence (audit K4)
    assert re.search(r"\n<<<ROLE-(\w+)>>>\nInhaberin\n<<<END-\1>>>\n", text)
    # the survey's length and the thesis topic come from the profile, not
    # from the prompt: v4 said "15 minutes" and the first owner's topic
    assert "20 minutes" in text
    assert "15 minutes" not in text
    assert re.search(
        r"\n<<<TOPIC-(\w+)>>>\nwie KMU Personal finden\n<<<END-\1>>>\n", text
    )
    assert "customers and suppliers" not in text
