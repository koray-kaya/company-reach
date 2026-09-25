"""The one-click start (issue #44): the scripts parse, and the desktop app
runs this checkout's start script. Starting Docker and the page is tried by
hand; a test here would start real services."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"


@pytest.mark.parametrize("name", ["start.sh", "stop.sh", "make-desktop-app.sh"])
def test_the_script_parses_and_is_executable(name):
    script = SCRIPTS / name
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    assert script.stat().st_mode & 0o111


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("osacompile"), reason="macOS only"
)
def test_the_desktop_app_runs_the_start_script(tmp_path):
    app = tmp_path / "Company Reach.app"
    out = subprocess.run(
        [str(SCRIPTS / "make-desktop-app.sh"), str(app)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    compiled = app / "Contents" / "Resources" / "Scripts" / "main.scpt"
    assert compiled.exists()
    source = subprocess.run(
        ["osadecompile", str(compiled)], capture_output=True, text=True
    ).stdout
    assert f"{SCRIPTS.resolve()}/start.sh" in source
    assert "display dialog" in source  # a failure is shown, not swallowed


def test_the_desktop_app_path_must_end_in_app(tmp_path):
    # the path is deleted before the app is written: a folder given by
    # mistake must not be
    folder = tmp_path / "Desktop"
    folder.mkdir()
    (folder / "note.txt").write_text("keep me")
    out = subprocess.run(
        [str(SCRIPTS / "make-desktop-app.sh"), str(folder)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode != 0
    assert ".app" in out.stderr
    assert (folder / "note.txt").read_text() == "keep me"


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("osacompile"), reason="macOS only"
)
def test_the_build_leaves_no_temporary_file(tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    out = subprocess.run(
        [str(SCRIPTS / "make-desktop-app.sh"), str(tmp_path / "X.app")],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(scratch)},
    )
    assert out.returncode == 0, out.stderr
    assert list(scratch.iterdir()) == []
