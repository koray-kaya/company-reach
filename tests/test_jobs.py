"""Commands the front page starts in the background (issue #44).

A button names an action; the action maps to a fixed argument list, and the
one value a form may carry is checked before it is added. Nothing reaches a
shell. The runner is tested with a stand-in for `python -m company_reach`,
so no test here calls a model or the register.
"""

import json
import subprocess
import sys
import time

import pytest

from company_reach.review.jobs import JobBusy, Jobs, argv_for

# --- what each button runs ---------------------------------------------------


@pytest.mark.parametrize(
    ("action", "value", "argv"),
    [
        ("round", "", ["run", "--target", "10"]),
        ("score", "", ["score", "--limit", "200"]),
        ("doctor", "", ["doctor"]),
        ("retry", "r8e2719e4", ["retry", "r8e2719e4"]),
        ("redraft", "m6-e2e-1", ["redraft", "m6-e2e-1"]),
        ("pool", "3203", ["pool", "--municipality", "3203"]),
        ("pool", " 3203, 3443 ", ["pool", "--municipality", "3203,3443"]),
    ],
)
def test_each_action_is_a_fixed_argument_list(action, value, argv):
    assert argv_for(action, value) == argv


@pytest.mark.parametrize(
    ("action", "value"),
    [
        ("retry", ""),
        ("retry", "--no-site"),  # an option, not a run
        ("retry", "../x"),
        ("redraft", "r1; rm -rf /"),
        ("pool", ""),
        ("pool", "3203 --run-id x"),
        ("pool", "abc"),
        ("pool", "\u0663\u0662\u0660\u0663"),  # digits, but not ASCII ones
        ("forget", "info@muster.ch"),  # not a button
        ("", ""),
    ],
)
def test_anything_else_is_refused(action, value):
    with pytest.raises(ValueError):
        argv_for(action, value)


def test_the_package_runs_as_a_module():
    # the runner starts `python -m company_reach`, with the command's name
    out = subprocess.run(
        [sys.executable, "-m", "company_reach", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0
    assert "company-reach" in out.stdout
    assert "doctor" in out.stdout


# --- running one -------------------------------------------------------------


def fake(code: str) -> list[str]:
    """A stand-in for `python -m company_reach`: runs `code`, which sees the
    command's arguments in sys.argv[1:]."""
    return [sys.executable, "-c", code]


def wait(job, seconds: float = 20) -> None:
    end = time.monotonic() + seconds
    while job.running and time.monotonic() < end:
        time.sleep(0.05)
    assert not job.running, "the job did not finish"


def test_a_job_writes_its_output_to_a_log(tmp_path):
    jobs = Jobs(tmp_path, command=fake("import sys; print('args', sys.argv[1:])"))
    job = jobs.start("round")
    wait(job)
    assert job.returncode == 0
    text = job.log.read_text()
    assert text.startswith("$ company-reach run --target 10\n")
    assert "args ['run', '--target', '10']" in text
    assert job.log.parent == tmp_path / "jobs"


def test_a_failed_command_keeps_its_exit_code_and_error(tmp_path):
    jobs = Jobs(tmp_path, command=fake("import sys; sys.exit('no model answered')"))
    job = jobs.start("doctor")
    wait(job)
    assert job.returncode == 1
    assert "no model answered" in job.tail()


def test_one_job_at_a_time(tmp_path):
    jobs = Jobs(tmp_path, command=fake("import time; time.sleep(30)"))
    first = jobs.start("score")
    try:
        with pytest.raises(JobBusy, match="score"):
            jobs.start("round")
    finally:
        first.process.kill()
        first.process.wait()
    # once it has ended, the next one may start
    jobs.command = fake("pass")
    wait(jobs.start("round"))


def test_a_question_ends_the_command_instead_of_hanging(tmp_path):
    # a command that asks (typer.confirm) must not wait for a keyboard the
    # page does not have
    jobs = Jobs(tmp_path, command=fake("input('Write a new set? ')"))
    job = jobs.start("doctor")
    wait(job, seconds=10)
    assert job.returncode != 0


def test_the_tail_is_the_end_of_the_log(tmp_path):
    jobs = Jobs(tmp_path, command=fake("for i in range(100): print('line', i)"))
    job = jobs.start("score")
    wait(job)
    tail = job.tail(lines=3).splitlines()
    assert tail == ["line 97", "line 98", "line 99"]


def test_old_logs_are_removed(tmp_path):
    # the logs name companies and people: only the last few are kept
    jobs = Jobs(tmp_path, command=fake("pass"), keep=3)
    for _ in range(5):
        wait(jobs.start("doctor"))
    assert len(list((tmp_path / "jobs").glob("*.log"))) == 3
    assert jobs.current.log.exists()


def test_a_command_started_before_the_page_restarted_still_blocks(tmp_path):
    # stop.sh and start.sh restart the page; a round it started runs on
    first = Jobs(tmp_path, command=fake("import time; time.sleep(30)"))
    job = first.start("round")
    try:
        again = Jobs(tmp_path, command=fake("pass"))
        assert again.current is not None
        assert again.current.running
        assert again.current.action == "round"
        assert again.current.tail().startswith("$ company-reach run --target 10")
        with pytest.raises(JobBusy, match="round"):
            again.start("score")
    finally:
        job.process.kill()
        job.process.wait()
    assert not again.current.running
    wait(again.start("score"))


def test_a_vanished_log_reads_as_empty(tmp_path):
    jobs = Jobs(tmp_path, command=fake("pass"))
    job = jobs.start("doctor")
    wait(job)
    job.log.unlink()
    assert job.tail() == ""


def test_a_process_number_reused_by_another_program_is_not_a_job(tmp_path):
    # after a restart of the machine the recorded pid can belong to anything;
    # it must not keep the buttons locked
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "jobs").mkdir()
        (tmp_path / "jobs" / "current.json").write_text(
            json.dumps(
                {
                    "action": "round",
                    "argv": ["run", "--target", "10"],
                    "log": "x-round.log",
                    "started": "2026-09-25T10:00:00",
                    "pid": other.pid,
                }
            )
        )
        assert Jobs(tmp_path, command=fake("pass")).current is None
    finally:
        other.kill()
        other.wait()
