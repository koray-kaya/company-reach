"""Who is running this tool and what they are looking for.

Kept in `profile.toml` rather than `.env` because most fields are prose that
the user will rewrite and reread, and TOML keeps multi-line text readable.
The file is gitignored: the goal is a research decision, and `about_me` and
`[sender]` name a person. `tomllib` is in the standard library since Python
3.11.

Two sections feed the invitation's frame, the part code writes around the
model's one sentence (`tools/invitation.py`): `[sender]` says who writes,
`[invitation]` what the mail may promise. Both are optional until drafting,
so scoring and site finding run without them; `draft` refuses to start and
`doctor` names what is missing. Every sentence whose fact is not confirmed
yet (a results offer, no login, a reminder) stays off until its boolean is
set to true here.
"""

import hashlib
import re
import tomllib
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from company_reach.errors import ProfileError

_WHITESPACE = re.compile(r"\s+")


class Sender(BaseModel):
    """Who signs the invitation. `extra="forbid"`: a misspelt key would
    otherwise vanish, and the mail would go out without the field."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    # e.g. "Masterstudentin, <Hochschule>"; the closing's third line
    affiliation: str = ""
    # the school as the subject names it: "Masterarbeit an der <school_short>"
    school_short: str = ""
    place: str = ""  # optional: "studiere an der <school_short> in <place>"
    supervisor: str = ""  # optional, and only with the supervisor's consent


class Invitation(BaseModel):
    """What the mail says about the survey. Every boolean defaults to false:
    a sentence is added only once its fact is confirmed."""

    model_config = ConfigDict(extra="forbid")

    # the end of "Es geht darum, ...", e.g. "wie KMU zu Kunden kommen"
    topic: str = ""
    minutes: int = 15
    closes: date | None = None  # "offen bis Freitag, 30. Oktober"
    offer_results: bool = False  # the last page offers the results
    no_login: bool = False  # the form needs no account
    reminder: bool = False  # one reminder is allowed (needs ethics approval)
    experiment: bool = False  # the "voll" / "kurz" length A/B
    # false: the mail ends with the closing line only, for a mail client that
    # adds the same signature itself
    sign_in_body: bool = True


class Profile(BaseModel):
    goal: str
    about_me: str = ""
    # Every invitation links here, tagged with the company's UID. Optional
    # until drafting: scoring and site finding run without it, and `doctor`
    # reports its absence before a run that would draft.
    survey_url: str = ""
    sender: Sender = Field(default_factory=Sender)
    invitation: Invitation = Field(default_factory=Invitation)

    def drafting_gaps(self) -> list[str]:
        """The fields an invitation needs that this profile leaves empty,
        in the order the file lists them. Empty means drafting may start."""
        required = {
            "survey_url": self.survey_url,
            "sender.name": self.sender.name,
            "sender.affiliation": self.sender.affiliation,
            "sender.school_short": self.sender.school_short,
            "invitation.topic": self.invitation.topic,
        }
        return [key for key, value in required.items() if not value.strip()]


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
    try:
        return Profile(
            goal=goal,
            about_me=str(data.get("about_me", "")).strip(),
            survey_url=survey_url,
            sender=Sender(**_stripped(data.get("sender", {}))),
            invitation=Invitation(**_stripped(data.get("invitation", {}))),
        )
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        raise ProfileError(f"{path}: {problems}") from e


def _stripped(section: dict) -> dict:
    """Text values without the spaces a hand-edited file picks up."""
    return {k: v.strip() if isinstance(v, str) else v for k, v in section.items()}


def goal_hash(goal: str) -> str:
    """Key for the score cache. Normalised so that reformatting a goal does
    not throw away scores, while rewording it does — which is correct, since
    a different goal deserves different scores."""
    normalised = _WHITESPACE.sub(" ", goal).strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]
