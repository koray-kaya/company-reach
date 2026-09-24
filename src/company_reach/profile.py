"""Who is running this tool and what they are looking for.

Kept in `profile.toml` rather than `.env` because both fields are prose that
the user will rewrite and reread, and TOML keeps multi-line text readable.
The file is gitignored: the goal is a research decision, and `about_me` names
a person. `tomllib` is in the standard library since Python 3.11.
"""

import hashlib
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel

from company_reach.errors import ProfileError

_WHITESPACE = re.compile(r"\s+")


class Profile(BaseModel):
    goal: str
    about_me: str = ""
    # Every invitation links here, tagged with the company's UID. Optional
    # until drafting: scoring and site finding run without it, and `doctor`
    # reports its absence before a run that would draft.
    survey_url: str = ""


def load_profile(path: Path) -> Profile:
    if not path.is_file():
        raise ProfileError(
            f"no profile.toml at {path} — copy profile.toml.example and "
            f"write your goal in it"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{path} is not valid TOML: {e}") from e
    goal = str(data.get("goal", "")).strip()
    if not goal:
        raise ProfileError(f"{path} has no goal")
    survey_url = str(data.get("survey_url", "")).strip()
    if survey_url and not survey_url.startswith("https://"):
        raise ProfileError(f"{path}: survey_url must start with https://")
    return Profile(
        goal=goal,
        about_me=str(data.get("about_me", "")).strip(),
        survey_url=survey_url,
    )


def goal_hash(goal: str) -> str:
    """Key for the score cache. Normalised so that reformatting a goal does
    not throw away scores, while rewording it does — which is correct, since
    a different goal deserves different scores."""
    normalised = _WHITESPACE.sub(" ", goal).strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]
