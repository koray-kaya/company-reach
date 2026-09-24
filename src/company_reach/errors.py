"""Typed errors. Each one names a boundary the system can fail at, so a
caller can catch the one it knows how to handle and let the rest travel.
A finding ("this company has no website") is never an error — see the
design note on findings vs errors."""


class CompanyReachError(RuntimeError):
    """Base class, so `except CompanyReachError` catches all of ours."""


class PromptError(CompanyReachError):
    """A prompt file is missing, malformed, or a variable was not supplied."""


class ProfileError(CompanyReachError):
    """profile.toml is missing, malformed, or has no goal."""


class LlmError(CompanyReachError):
    """The model endpoint failed, or its answer did not match the schema."""


class SearchError(CompanyReachError):
    """No search provider could answer. Raised by M4; declared here because
    the wrapper that maps errors to result rows exists from M3 on."""


class FetchError(CompanyReachError):
    """A page could not be fetched or carried no usable text. Raised by M5."""


class DoctorError(CompanyReachError):
    """A pre-flight check failed; the run must not start."""


class ShabError(CompanyReachError):
    """The SHAB API could not be asked, or answered in a way that shows its
    filter was ignored. A company with no notices is `[]`, not this."""
