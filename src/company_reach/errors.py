"""Typed errors. Each one names a boundary the system can fail at, so a
caller can catch the one it knows how to handle and let the rest travel.
A finding ("this company has no website") is never an error — see the
design note on findings vs errors."""


class CompanyReachError(RuntimeError):
    """Base class, so `except CompanyReachError` catches all of ours."""


class PromptError(CompanyReachError):
    """A prompt file is missing, malformed, or a variable was not supplied."""


class LlmError(CompanyReachError):
    """The model endpoint failed, or its answer did not match the schema."""


class DoctorError(CompanyReachError):
    """A pre-flight check failed; the run must not start."""
