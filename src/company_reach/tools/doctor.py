"""Pre-flight checks. A scoring run takes twenty minutes; none of it should
be spent discovering that the key is wrong. Every check runs even when an
earlier one fails, because one command should report every problem at once
rather than one per attempt."""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from company_reach.errors import LlmError, ProfileError
from company_reach.profile import load_profile
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import connect, init_db

MARKER = "COMPANY-REACH-OK"
_PROMPTS = ("criteria", "score", "doctor")


class _Probe(BaseModel):
    marker: str


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _settings_check(settings: Settings) -> Check:
    return Check(
        "settings",
        True,
        f"model={settings.llm_model} effort={settings.llm_reasoning_effort} "
        f"max_tokens={settings.llm_max_tokens} concurrency={settings.llm_concurrency}",
    )


def _prompts_check() -> Check:
    try:
        versions = {name: llm.load_prompt(name)[0] for name in _PROMPTS}
    except Exception as e:
        return Check("prompts", False, str(e))
    return Check("prompts", True, ", ".join(f"{k}@{v}" for k, v in versions.items()))


def _database_check(settings: Settings) -> Check:
    """Writes to a temporary file, not the real database: doctor must never
    change data it is only inspecting."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe.db"
            init_db(path)
            with connect(path) as conn:
                conn.execute(
                    "insert into suppression (key, reason, added_at) values (?,?,?)",
                    ("probe", "doctor", "now"),
                )
            with connect(path) as conn:
                n = conn.execute("select count(*) from suppression").fetchone()[0]
        if n != 1:
            return Check("database", False, f"wrote 1 row, read back {n}")
    except Exception as e:
        return Check("database", False, f"{type(e).__name__}: {e}")
    return Check("database", True, f"schema applies, WAL ok ({settings.db_path})")


async def _endpoint_check(settings: Settings) -> Check:
    try:
        probe, prov = await llm.ask("doctor", _Probe, settings=settings, marker=MARKER)
    except Exception as e:
        return Check("endpoint", False, f"{type(e).__name__}: {str(e)[:200]}")
    if probe.marker != MARKER:
        return Check(
            "endpoint",
            False,
            f"marker came back as {probe.marker!r}, expected {MARKER!r} — "
            "the model is not following the schema, or the prompt was clipped",
        )
    return Check(
        "endpoint",
        True,
        f"{prov.model} answered in {prov.seconds:.1f}s, schema honoured "
        f"({prov.completion_tokens} tokens)",
    )


async def _budget_check(settings: Settings) -> Check:
    """Ask for an answer that cannot fit. An endpoint that ignores the budget
    would leave a runaway generation unbounded, and this tool's protection
    against truncation would be silently useless."""
    try:
        await llm.ask("doctor", _Probe, settings=settings, max_tokens=16, marker=MARKER)
    except LlmError as e:
        if "truncated" in str(e):
            return Check("token budget", True, "max_completion_tokens is honoured")
        return Check("token budget", False, str(e)[:200])
    except Exception as e:
        return Check("token budget", False, f"{type(e).__name__}: {str(e)[:200]}")
    return Check(
        "token budget",
        False,
        "a 16-token budget produced a full answer: the endpoint ignored it",
    )


def _profile_check(settings: Settings) -> Check:
    """The survey link goes into every draft, so a run that drafts without
    one would fail at its first company rather than here."""
    try:
        profile = load_profile(settings.profile_path)
    except ProfileError as e:
        return Check("profile", False, str(e))
    if not profile.survey_url:
        return Check(
            "profile",
            False,
            f"{settings.profile_path} has no survey_url; every draft links to it",
        )
    return Check("profile", True, f"goal set, survey_url={profile.survey_url}")


RETENTION_DAYS = 365


def _retention_check(settings: Settings) -> Check:
    """Informational, never a failure: how many companies a purge would
    clear (#27). Old data is a reason to run `purge`, not to refuse a run."""
    from datetime import date, timedelta

    from company_reach.forget import stale_uids

    cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
    with connect(settings.db_path) as conn:
        n = len(stale_uids(conn, cutoff=cutoff))
    if n == 0:
        return Check("retention", True, f"nothing older than {RETENTION_DAYS} days")
    return Check(
        "retention",
        True,
        f"{n} companies older than {RETENTION_DAYS} days hold personal data; "
        f"run: company-reach purge --older-than {RETENTION_DAYS}",
    )


async def run_checks(settings: Settings) -> list[Check]:
    return [
        _settings_check(settings),
        _prompts_check(),
        _database_check(settings),
        _profile_check(settings),
        _retention_check(settings),
        await _endpoint_check(settings),
        await _budget_check(settings),
    ]
