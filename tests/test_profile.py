import pytest

from company_reach.errors import ProfileError
from company_reach.profile import goal_hash, load_profile

TOML = """
goal = "Swiss companies in manufacturing or technical services."
about_me = "A master's student writing a thesis."
"""


def test_reads_goal_and_about_me(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text(TOML)
    profile = load_profile(path)
    assert profile.goal.startswith("Swiss companies")
    assert "thesis" in profile.about_me


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(ProfileError, match="profile.toml"):
        load_profile(tmp_path / "profile.toml")


def test_empty_goal_is_an_error(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('goal = ""\nabout_me = "x"\n')
    with pytest.raises(ProfileError, match="goal"):
        load_profile(path)


def test_hash_ignores_formatting_but_not_wording():
    a = "Swiss companies in manufacturing."
    assert goal_hash(a) == goal_hash("  Swiss   companies in   manufacturing.  ")
    assert goal_hash(a) == goal_hash("Swiss companies in Manufacturing.")
    assert goal_hash(a) != goal_hash("Swiss companies in trade.")


def test_hash_is_short_and_stable():
    h = goal_hash("anything")
    assert len(h) == 12
    assert h == goal_hash("anything")
