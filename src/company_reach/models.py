"""Pydantic records shared by the pipeline, the database layer and (later)
the LLM output schemas. Pydantic validates on construction, so a record that
exists is a record that is well-formed."""

import re

from pydantic import BaseModel, Field, field_validator

_UID_DIGITS = re.compile(r"\D")


class CompanyRecord(BaseModel):
    uid: str  # normalised: CHE + 9 digits, no dots
    name: str
    legal_form: str  # "0106" AG · "0107" GmbH
    municipality: str  # FSO id, e.g. "3203"
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    purpose: str
    purpose_head: str

    @field_validator("uid")
    @classmethod
    def _normalise_uid(cls, v: str) -> str:
        digits = _UID_DIGITS.sub("", v)
        if len(digits) != 9:
            raise ValueError(f"UID must carry 9 digits: {v!r}")
        return f"CHE{digits}"

    @property
    def uid_dotted(self) -> str:
        d = self.uid[3:]
        return f"CHE-{d[:3]}.{d[3:6]}.{d[6:]}"


class SelectionCriteria(BaseModel):
    """The goal, expanded into rules a scorer can apply. Produced by the model
    from one sentence, stored on the run and shown to the user before any
    scoring is paid for, so the rules are data he can read and correct."""

    must: list[str] = Field(description="all must hold for a good fit")
    must_not: list[str] = Field(description="any one of these rules a company out")
    positive_signals: list[str] = Field(description="raise the score when present")


class Score(BaseModel):
    """One company's fit with the goal. Also the schema the model must obey."""

    uid: str
    score: int = Field(ge=0, le=10)
    reason: str


class ScoreBatch(BaseModel):
    """What one scoring call returns. The list is checked against the UIDs
    that were sent — the model may drop, duplicate or invent one."""

    scores: list[Score]
