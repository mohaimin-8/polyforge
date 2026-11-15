"""detach.py without starting anything: argument split and the Popen call it makes."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detach  # noqa: E402


def test_the_command_is_everything_after_the_separator():
    own, cmd = detach.split_command(["--log", "x.log", "--", "python", "run.py", "--", "--flag"])
    assert own == ["--log", "x.log"] and cmd == ["python", "run.py", "--", "--flag"]
    with pytest.raises(SystemExit):
        detach.split_command(["--log", "x.log", "python"])
    with pytest.raises(SystemExit):
        detach.split_command(["--log", "x.log", "--"])


def test_the_child_breaks_away_windowless_with_its_output_appended(tmp_path):
    seen = {}

    class Proc:
        pid = 4242

    def popen(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        kw["stdout"].write(b"line\n")
        return Proc()

    log = tmp_path / "logs" / "job.log"
    log.parent.mkdir()
    log.write_bytes(b"earlier\n")
    assert detach.start(["python", "run.py"], tmp_path, log, popen=popen) == 4242
    assert seen["cmd"] == ["python", "run.py"] and seen["cwd"] == str(tmp_path)
    assert seen["stdin"] is subprocess.DEVNULL and seen["stderr"] is subprocess.STDOUT
    if sys.platform == "win32":
        assert seen["creationflags"] & detach.BREAKAWAY and seen["creationflags"] & detach.NO_WINDOW
    assert log.read_bytes() == b"earlier\nline\n"
