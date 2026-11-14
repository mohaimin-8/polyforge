"""Run a command while asking Windows not to idle-sleep (no settings changed).

Long measurement and simulation jobs die when the laptop idles into sleep
(2026-11-13: a nine-hour cache replay lost at 17:46). This wrapper sets
ES_CONTINUOUS | ES_SYSTEM_REQUIRED for its own lifetime only, runs the
command, and returns its exit code; the request ends with the process, and
a user-initiated sleep (lid, power button, Start menu) still sleeps. On other
platforms it just runs the command.

    python tools/keepawake.py -- python run_all.py --preset full --workers 6
"""

from __future__ import annotations

import ctypes
import subprocess
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def main(argv: list[str]) -> int:
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    if not argv:
        print(__doc__)
        return 2
    held = False
    if sys.platform == "win32":
        held = bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    try:
        return subprocess.call(argv)
    finally:
        if held:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
