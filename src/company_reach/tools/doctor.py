"""Pre-flight checks. A scoring run takes twenty minutes; none of it should
be spent discovering that the key is wrong. Every check runs even when an
earlier one fails, because one command should report every problem at once
rather than one per attempt."""

import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from company_reach.errors import LlmError, ProfileError
from company_reach.models import Contact
from company_reach.nodes.check_draft import MAX_SENTENCE_CHARS
from company_reach.nodes.probe_search import PROBE_QUERY
from company_reach.profile import Profile, load_profile
from company_reach.settings import Settings
from company_reach.tools import llm
from company_reach.tools.db import connect, init_db
from company_reach.tools.invitation import assemble, subject, survey_link
from company_reach.tools.mailto import build
from company_reach.tools.search import _brave, ask_searxng, search

MARKER = "COMPANY-REACH-OK"
# Every prompt a run loads, so a broken header fails here rather than at the
# first company that reaches that node.
_PROMPTS = (
    "criteria",
    "score",
    "doctor",
    "pick_site",
    "pick_pages",
    "extract",
    "draft",
)


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
        f"max_tokens={settings.llm_max_tokens} concurrency={settings.llm_concurrency} "
        f"sending_approved={settings.sending_approved} "
        f"paid_fallback={'brave' if settings.brave_search_api_key else 'none'}",
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


MIN_DAYS_OPEN = 14
# The longest mail the frame can build: a 180-character sentence with
# umlauts, a SHAB name at a shared inbox (the longest routing line and
# privacy text), a long name and a long address. Fictional, of course.
_WORST_SENTENCE = (
    "Ich schreibe Ihnen, weil Ihre Käserei Milch von Bauernhöfen aus der Region "
    "zu Käse verarbeitet und ihn an Läden, Hotels, Restaurants und Märkte in der "
    "ganzen Ostschweiz liefert, auch über Grosshändler."
)[: MAX_SENTENCE_CHARS - 1] + "."
_WORST_CONTACT = Contact(
    name="Dr. Katharina Beispiel-Hinterberger",
    role="Präsidentin des Verwaltungsrates",
    email="info@beispiel-hinterberger-kaeserei.example",
    email_kind="generic",
    source="shab",
)
# `mailto:` links must stay under 2,000 encoded characters; 100 are left for
# a name or an address longer than the worst case above.
_MAILTO_WARN = 1900
# A field never filled in: "[Hochschule]", "<Name>", "XY", "..."
_UNFILLED = re.compile(r"[\[\]{}<>]|\bXY\b|\bXX\b|\.\.\.|…")


def _profile_check(settings: Settings) -> Check:
    """Everything the invitation takes from the profile, checked before a
    run rather than at its first company: the survey link, the [sender] and
    [invitation] fields drafting needs, a closing date far enough ahead, no
    field left as a placeholder, and room in a `mailto:` link for the longest
    mail the frame can build."""
    try:
        profile = load_profile(settings.profile_path)
    except ProfileError as e:
        return Check("profile", False, str(e))
    found: list[str] = []
    if gaps := profile.drafting_gaps():
        found.append(f"missing for drafting: {', '.join(gaps)}")
    host = urlsplit(profile.survey_url).hostname or ""
    if host == "example" or host.endswith(".example"):
        found.append(
            f"survey_url is a placeholder ({host}); drafts would be unsendable"
        )
    fields = {
        **{f"sender.{k}": v for k, v in profile.sender.model_dump().items()},
        "invitation.topic": profile.invitation.topic,
    }
    for key, value in fields.items():
        if isinstance(value, str) and (unfilled := _UNFILLED.search(value)):
            found.append(f"{key} looks unfilled ({unfilled.group()}): {value}")
    closes = profile.invitation.closes
    if closes and (closes - date.today()).days < MIN_DAYS_OPEN:
        found.append(
            f"invitation.closes is {closes.isoformat()}, less than "
            f"{MIN_DAYS_OPEN} days ahead; a reader needs time to answer"
        )
    if not gaps and not found:
        length = _worst_case_mailto(profile)
        if length >= _MAILTO_WARN:
            found.append(
                f"the longest mail the frame can build encodes to {length} "
                f"characters in a mailto: link (limit 2,000); shorten a "
                "[sender] or [invitation] text"
            )
    if found:
        return Check("profile", False, "; ".join(found))
    return Check(
        "profile",
        True,
        f"goal set, survey_url={profile.survey_url}, sender "
        f"{profile.sender.school_short}, closes "
        f"{closes.isoformat() if closes else 'not set'}",
    )


def _worst_case_mailto(profile: Profile) -> int:
    link = survey_link(profile.survey_url, "CHE000000046")
    body = assemble(
        _WORST_CONTACT,
        _WORST_SENTENCE,
        link=link,
        sender=profile.sender,
        inv=profile.invitation,
    )
    title = subject(_WORST_CONTACT, profile.sender, profile.invitation)
    return build(_WORST_CONTACT.email or "", title, body).length


async def _search_check(settings: Settings) -> Check:
    """The query probe_search asks before every run. Nothing back means the
    run would stop there, after scoring was already paid for. SearXNG is
    asked alone: through `search`, a working Brave would stand in for it and
    hide that the free provider is down."""
    try:
        asked = await ask_searxng(PROBE_QUERY, settings=settings, limit=3)
    except Exception as e:  # a check reports; it never stops the others
        return Check("search", False, f"{type(e).__name__}: {str(e)[:200]}")
    if asked.error is not None:
        return Check("search", False, f"SearchError: {asked.error[:200]}")
    results = asked.results
    if not results:
        return Check(
            "search",
            False,
            "the probe query returned nothing; engines may be suspended",
        )
    engines = sorted({r.engine for r in results if r.engine}) or ["unknown"]
    return Check("search", True, f"{len(results)} results from {', '.join(engines)}")


async def _engines_check(settings: Settings) -> Check:
    """The engines the no-website guard relies on must exist and be enabled.
    Round 1 found two of the three inactive in the pinned SearXNG image, so
    the guard could never fire."""
    url = f"{settings.searxng_url.rstrip('/')}/config"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            config = (await client.get(url)).json()
    except Exception as e:
        return Check("engines", False, f"cannot read {url}: {type(e).__name__}")
    enabled = {e["name"] for e in config.get("engines", []) if e.get("enabled")}
    wanted = [n.strip() for n in settings.baseline_engines.split(",") if n.strip()]
    missing = [n for n in wanted if n not in enabled]
    if missing:
        return Check(
            "engines", False, f"baseline engines not enabled: {', '.join(missing)}"
        )
    return Check("engines", True, ", ".join(wanted))


async def _brave_check(settings: Settings) -> Check:
    """The paid provider, asked directly with the probe query. Through
    `search` it would be asked only when SearXNG failed, so a rejected key
    would stay hidden behind a working SearXNG (review focus 5)."""
    if settings.brave_search_api_key is None:
        return Check("brave", True, "not configured")
    try:
        results = await _brave(PROBE_QUERY, settings=settings, limit=3)
    except Exception as e:  # a check reports; it never stops the others
        return Check("brave", False, f"{type(e).__name__}: {str(e)[:200]}")
    if not results:
        return Check("brave", False, "the probe query returned nothing")
    return Check("brave", True, f"{len(results)} results")


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
        await _search_check(settings),
        await _engines_check(settings),
        await _brave_check(settings),
        await _endpoint_check(settings),
        await _budget_check(settings),
    ]
