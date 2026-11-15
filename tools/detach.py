"""Start a long job outside the calling session's Windows job object.

An interactive tool session can run its commands inside a job object, and when
that session ends every process in the job is terminated, hidden or not (15 Nov 2026,
09:24: the post-WP11 chain, the CPU campaign queue and Docker Desktop died
together, then the laptop idled into sleep). The job allows breakaway, so a
child created with CREATE_BREAKAWAY_FROM_JOB leaves it and survives the
session. The child runs without a console window (its own console children
inherit the windowless console), its output is appended to --log, and its PID
is printed for anything that waits on it.

    python tools/detach.py --cwd <dir> --log <file> -- python tools/keepawake.py -- python <script> ...
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

# subprocess's names for the Win32 flags (absent off Windows)
BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
FLAGS = BREAKAWAY | NEW_GROUP | NO_WINDOW


def split_command(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """(this tool's arguments, the command after '--')."""
    argv = list(argv)
    if "--" not in argv:
        raise SystemExit("usage: detach.py [--cwd DIR] --log FILE -- COMMAND ...")
    i = argv.index("--")
    if i == len(argv) - 1:
        raise SystemExit("detach.py: no command after '--'")
    return argv[:i], argv[i + 1:]


def start(cmd: list[str], cwd: Path, log: Path, popen=subprocess.Popen) -> int:
    """Start cmd detached from the job, output appended to log; its PID."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        proc = popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                     creationflags=FLAGS if sys.platform == "win32" else 0, close_fds=True)
    return proc.pid


def main(argv: Sequence[str] | None = None) -> int:
    own, cmd = split_command(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", type=Path, default=Path.cwd())
    ap.add_argument("--log", type=Path, required=True)
    args = ap.parse_args(own)
    print(start(cmd, args.cwd.resolve(), args.log.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
