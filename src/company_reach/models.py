"""Pydantic records shared by the pipeline, the database layer and (later)
the LLM output schemas. Pydantic validates on construction, so a record that
exists is a record that is well-formed."""

import re
from typing import Literal

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


class RawScore(BaseModel):
    """What the model returns, deliberately unconstrained.

    Structured output enforces shape, not numeric range: guided decoding will
    keep `score` an integer but will happily emit 11. Validating the range
    here would make one stray number destroy the other 49 answers in the
    batch, so the range is checked per entry in `score_pool` instead and only
    the offending row is dropped."""

    uid: str
    score: int = Field(description="0 = certainly not, 10 = clearly the target")
    reason: str = Field(description="at most 20 words, English")


class ScoreBatch(BaseModel):
    """One scoring call's answer. The list is checked against the UIDs that
    were sent — the model may drop, duplicate or invent one."""

    scores: list[RawScore]


class Score(BaseModel):
    """A score that passed the checks and may be stored."""

    uid: str
    score: int = Field(ge=0, le=10)
    reason: str


Recommendation = Literal["send", "hold", "skip"]
ErrorKind = Literal["search", "fetch", "llm", "other"]


class CompanyResult(BaseModel):
    """What one company's child graph produced — a recommendation or a
    failure, never both and never neither once the wrapper is done with it.

    Literal types rather than free strings: a typo in `error_kind` would
    otherwise reach the results table and quietly break every count that
    groups by it. Pydantic rejects it here instead.
    """

    uid: str
    recommendation: Recommendation | None = None
    reason: str | None = None
    error_kind: ErrorKind | None = None
    error_text: str | None = None
