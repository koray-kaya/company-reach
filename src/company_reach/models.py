"""Pydantic records shared by the pipeline, the database layer and (later)
the LLM output schemas. Pydantic validates on construction, so a record that
exists is a record that is well-formed."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_UID_DIGITS = re.compile(r"\D")


def dotted_uid(uid: str) -> str:
    """`CHE000000003` -> `CHE-000.000.003`, the form SHAB's filter needs."""
    d = uid[3:]
    return f"CHE-{d[:3]}.{d[3:6]}.{d[6:]}"


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
        return dotted_uid(self.uid)


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


# --- the profile -------------------------------------------------------------


class RawPerson(BaseModel):
    """A person as the model read them off the page, unchecked."""

    name: str = Field(description="exactly as the page writes it")
    role: str | None = Field(default=None, description="their function, or null")
    email: str | None = Field(default=None, description="their address, or null")


class RawProfile(BaseModel):
    """What `extract.md` returns, before anything has been checked.

    Split from `CompanyProfile` for the reason `RawScore` is split from
    `Score`, and for one more that matters here. `Person.email_offsite` is
    `check_profile`'s verdict, and a field absent from the schema the model
    is given is a field the model cannot fill in. On the one boundary this
    milestone exists to hold, "who wrote this field" should be answerable
    from the type rather than from reading the node.
    """

    description: str = Field(
        description="what the company makes or does, two or three sentences, English"
    )
    size_signal: str | None = Field(
        default=None,
        description="anything the pages say about how many people work there, or null",
    )
    persons: list[RawPerson] = Field(
        default_factory=list,
        description="people named on the pages; empty when the pages name none",
    )
    addresses: list[str] = Field(
        default_factory=list, description="postal addresses, as written"
    )
    distributor_only: bool = Field(
        default=False, description="true when the company only resells what others make"
    )
    foreign_group: bool = Field(
        default=False, description="true when the site belongs to a foreign parent"
    )


class Person(BaseModel):
    """A person `check_profile` has been over.

    `email_offsite` is set by that node and nowhere else: true when the
    address is real but its domain is not the verified site's, which is how a
    hostile page plants a contact (`audit-2026-09-19.md:177`). The person is
    kept rather than dropped — the mark is what M6 reads to hold them.
    """

    name: str
    role: str | None = None
    email: str | None = None
    email_offsite: bool = False


class CompanyProfile(BaseModel):
    """What the company's own pages say, after the checks.

    A profile naming nobody is a valid profile and a finding: the pages were
    read and named no one. "We never read the pages" is the absence of a
    profile, not an empty one.
    """

    description: str
    size_signal: str | None = None
    persons: list[Person] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    distributor_only: bool = False
    foreign_group: bool = False


class ShabPerson(BaseModel):
    """A person as one SHAB notice names them.

    Not a `Person`: the register says who held a role on a date, never who
    holds it now, so the date and the notice travel with the name. `departed`
    marks an entry the notice itself strikes out ("Ausgeschiedene",
    "radiée") — the register naming someone is not always the register
    naming a current role-holder.
    """

    name: str
    role: str | None = None
    departed: bool = False
    published: str  # ISO date of the notice
    source_url: str


Recommendation = Literal["send", "hold", "skip"]
ErrorKind = Literal["search", "fetch", "llm", "shab", "other"]


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
