"""Commands the front page starts in the background, one at a time.

The page never builds a shell line. A button names an action, the action
maps to a fixed argument list, and the one value a form may carry — a run id
or municipality numbers — is checked before it is added. The command runs as
`python -m company_reach …` in a child process with no stdin, so a question
it might ask ends it instead of waiting for a keyboard the page does not
have. Its output goes to a log under `data/jobs/`, which the page reads back.

The logs name companies and sometimes people, so they stay in the data
folder and only the last few are kept.
"""

import os
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# a run id as `run` makes them (r1a2b3c4d) or as a person typed one (m6-e2e-1);
# never starting with "-", so it cannot be read as an option
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# FSO municipality numbers, one or a comma list
_MUNICIPALITIES = re.compile(r"\d{1,4}(,\d{1,4})*")

FIXED = {
    "round": ["run", "--target", "10"],
    "score": ["score", "--limit", "200"],
    "doctor": ["doctor"],
}


def argv_for(action: str, value: str = "") -> list[str]:
    """The command line of one button, or ValueError."""
    if action in FIXED:
        return list(FIXED[action])
    if action in ("retry", "redraft"):
        if not _RUN_ID.fullmatch(value):
            raise ValueError(f"not a run id: {value!r}")
        return [action, value]
    if action == "pool":
        numbers = value.replace(" ", "")
        if not _MUNICIPALITIES.fullmatch(numbers):
            raise ValueError(f"not municipality numbers: {value!r}")
        return ["pool", "--municipality", numbers]
    raise ValueError(f"unknown action: {action!r}")


class JobBusy(Exception):
    """Another command is still running."""


@dataclass
class Job:
    action: str
    argv: list[str]
    log: Path
    started: datetime
    process: subprocess.Popen

    @property
    def returncode(self) -> int | None:
        return self.process.poll()

    @property
    def running(self) -> bool:
        return self.returncode is None

    def tail(self, lines: int = 40) -> str:
        with self.log.open(encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, maxlen=lines))


class Jobs:
    """The page's one slot for a running command."""

    def __init__(
        self,
        data_dir: Path,
        *,
        command: list[str] | None = None,
        keep: int = 20,
    ) -> None:
        self.dir = data_dir / "jobs"
        # how `company-reach` is started; a test hands in a stand-in
        self.command = command or [sys.executable, "-m", "company_reach"]
        self.keep = keep
        self.current: Job | None = None

    def start(self, action: str, value: str = "") -> Job:
        argv = argv_for(action, value)
        if self.current is not None and self.current.running:
            raise JobBusy(f"{self.current.action} is still running")
        self.dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        log = self.dir / f"{started:%Y%m%d-%H%M%S-%f}-{action}.log"
        with log.open("w", encoding="utf-8") as out:
            out.write(f"$ company-reach {' '.join(argv)}\n")
            out.flush()
            process = subprocess.Popen(
                [*self.command, *argv],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                # print() reaches the log line by line, not at the end
                env=os.environ | {"PYTHONUNBUFFERED": "1"},
                # the command finishes even if the page is stopped
                start_new_session=True,
            )
        self.current = Job(action, argv, log, started, process)
        self._prune()
        return self.current

    def _prune(self) -> None:
        logs = sorted(self.dir.glob("*.log"))
        for old in logs[: -self.keep]:
            old.unlink(missing_ok=True)
