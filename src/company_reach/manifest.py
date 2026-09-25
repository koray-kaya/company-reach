"""One JSON file per run, so a run can be explained without the database.

Written twice: once at the start, with everything that was decided before any
work happened, and once at the end, with what came out. The start write is the
useful one — a run that crashes still leaves a record of what it was trying to
do, and `status` stays "running" to say so.

Nothing here is a secret. `Settings` holds the API key as a `SecretStr`, and
the fields copied below are named one by one rather than dumped, because a
manifest is written to disk and read by a human months later.
"""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from company_reach.models import StoredCriteria
from company_reach.profile import goal_hash
from company_reach.settings import Settings
from company_reach.tools.db import now
from company_reach.tools.llm import PROMPT_DIR, load_prompt

# The settings that change what a run produces. Timeouts and URLs are left
# out: they change how it gets there, not what comes back.
_RECORDED_SETTINGS = (
    "batch_size",
    "draw_min_score",
    "max_batches_per_run",
    "score_batch_size",
    "score_limit",
    "llm_model",
    "llm_reasoning_effort",
    "llm_structured_method",
    "llm_max_tokens",
    "llm_concurrency",
)


def manifest_path(run_id: str, *, settings: Settings) -> Path:
    return settings.data_dir / "runs" / run_id / "manifest.json"


def _prompt_versions() -> dict[str, dict[str, str]]:
    """Version and file digest for every prompt. The version says which prompt
    was used; the sha says whether the file was edited without the version
    being raised — which is the failure the score cache cannot see."""
    prompts: dict[str, dict[str, str]] = {}
    for path in sorted(
        (p for p in PROMPT_DIR.iterdir() if p.name.endswith(".md")),
        key=lambda p: p.name,
    ):
        name = path.name.removesuffix(".md")
        version, _ = load_prompt(name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        prompts[name] = {"version": version, "sha256": digest}
    return prompts


def _git_commit() -> str | None:
    """None rather than a missing key: "we could not tell" and "we did not
    look" should not read the same six months later."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def _write(run_id: str, *, settings: Settings, fields: dict[str, Any]) -> Path:
    """Merge into whatever is already there, so the end write cannot lose what
    the start write recorded."""
    path = manifest_path(run_id, settings=settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
    current.update(fields)
    path.write_text(
        json.dumps(current, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def start_manifest(
    run_id: str,
    *,
    settings: Settings,
    goal: str,
    seed: int,
    about_me: str = "",
    criteria: StoredCriteria | None = None,
) -> Path:
    """`criteria` are the goal's stored ones, the rules the drawn companies
    were scored against; None for a goal whose scores predate stored
    criteria, which the file then says rather than leaves out."""
    return _write(
        run_id,
        settings=settings,
        fields={
            "run_id": run_id,
            "status": "running",
            "started_at": now(),
            "goal": goal,
            "goal_hash": goal_hash(goal),
            "about_me": about_me,
            "criteria": criteria.criteria.model_dump() if criteria else None,
            "criteria_hash": criteria.criteria_hash if criteria else None,
            "seed": seed,
            "git_commit": _git_commit(),
            "prompts": _prompt_versions(),
            "settings": {name: getattr(settings, name) for name in _RECORDED_SETTINGS},
        },
    )


def finish_manifest(
    run_id: str,
    *,
    settings: Settings,
    status: str,
    counts: dict[str, int],
    criteria: Any = None,
    reason: str | None = None,
) -> Path:
    fields: dict[str, Any] = {
        "status": status,
        "finished_at": now(),
        "counts": counts,
    }
    # Why a failed run stopped, so the file answers what the terminal showed
    # once and then scrolled away. Written on every finish: a resumed run
    # that succeeds must not keep the reason of the attempt before it.
    fields["reason"] = reason
    if criteria is not None:
        fields["criteria"] = (
            criteria.model_dump() if hasattr(criteria, "model_dump") else criteria
        )
    return _write(run_id, settings=settings, fields=fields)
