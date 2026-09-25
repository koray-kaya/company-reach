"""Commands the front page starts in the background, one at a time.

The page never builds a shell line. A button names an action, the action
maps to a fixed argument list, and the one value a form may carry — a run id
or municipality numbers — is checked before it is added. The command runs as
`python -m company_reach …` in a child process with no stdin, so a question
it might ask ends it instead of waiting for a keyboard the page does not
have. Its output goes to a log under `data/jobs/`, which the page reads back.

The logs name companies and sometimes people, so they stay in the data
folder and only the last few are kept.

A command outlives the page that started it (stop.sh, start.sh), so the
running one is also written to `data/jobs/current.json`: a page started
again shows it and starts nothing else until it has ended.
"""

import json
import os
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from itertools import islice
from pathlib import Path

# a run id as `run` makes them (r1a2b3c4d) or as a person typed one (m6-e2e-1);
# never starting with "-", so it cannot be read as an option
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# FSO municipality numbers, one or a comma list
_MUNICIPALITIES = re.compile(r"[0-9]{1,4}(,[0-9]{1,4})*")

# `run` prints "run <id> — if it stops, …" as its first line
_RUN_LINE = re.compile(r"run (\S+) ")

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


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _runs(pid: int, argv: list[str]) -> bool:
    """The process still runs this command. After a restart of the machine
    the number can belong to any program; the command line tells them apart."""
    shown = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
    )
    return shown.returncode == 0 and " ".join(argv) in shown.stdout


@dataclass
class Job:
    action: str
    log: Path
    started: datetime
    pid: int
    # None for a command an earlier page started: it is only watched
    process: subprocess.Popen | None = None
    argv: list[str] = field(default_factory=list)

    @property
    def returncode(self) -> int | None:
        """The exit code; None while it runs, and for a command an earlier
        page started, whose code nobody here can read."""
        return self.process.poll() if self.process else None

    @property
    def running(self) -> bool:
        if self.process:
            return self.process.poll() is None
        return _alive(self.pid) and _runs(self.pid, self.argv)

    @property
    def run_id(self) -> str | None:
        """The run this command works on: named in `retry` and `redraft`,
        printed by `run` in its first line. None for anything else."""
        if self.argv[:1] in (["retry"], ["redraft"]) and len(self.argv) > 1:
            return self.argv[1]
        try:
            with self.log.open(encoding="utf-8", errors="replace") as f:
                for line in islice(f, 5):
                    if found := _RUN_LINE.match(line):
                        return found.group(1)
        except FileNotFoundError:
            pass
        return None

    def tail(self, lines: int = 40) -> str:
        try:
            with self.log.open(encoding="utf-8", errors="replace") as f:
                return "".join(deque(f, maxlen=lines))
        except FileNotFoundError:
            return ""


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
        self.state = self.dir / "current.json"
        self.current: Job | None = self._left_running()

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
        self.current = Job(action, log, started, process.pid, process, argv)
        self.state.write_text(
            json.dumps(
                {
                    "action": action,
                    "argv": argv,
                    "log": log.name,
                    "started": started.isoformat(),
                    "pid": process.pid,
                }
            )
        )
        self._prune()
        return self.current

    def _left_running(self) -> Job | None:
        """The command an earlier page started, while it still runs."""
        try:
            left = json.loads(self.state.read_text())
            job = Job(
                left["action"],
                self.dir / left["log"],
                datetime.fromisoformat(left["started"]),
                int(left["pid"]),
                argv=[str(a) for a in left["argv"]],
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return job if job.running else None

    def _prune(self) -> None:
        logs = sorted(self.dir.glob("*.log"))
        for old in logs[: -self.keep]:
            old.unlink(missing_ok=True)
