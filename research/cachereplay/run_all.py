"""WP6 end to end in one command (programme 2).

    python run_all.py --preset full --workers 6     # every stage at full size; runtime in README.md
    python run_all.py --preset smoke                # the same stages on <= 2,000 prompts each

Stages run as separate processes, in this order; each one's output goes to
out/<preset>/logs/<stage>.log and a failing stage stops the run:
  replay      replay.py: embed the LMSYS first turns (cached under data/), replay the grid
  gomem       gocheck mem: heap bytes per cache entry on the run's length sample (needs Go)
  precision   precision.py: QQP precision-recall (downloads ~37 MB once) and the LMSYS proxy
  oldcheck    oldcheck.py: the old protocol reproduced (against the published table on `full`)
  fit         fit.py: curves with bootstrap intervals, MB per entry, plant2_cache.json
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STAGES = ("replay", "gomem", "precision", "oldcheck", "fit")


def commands(preset: str, workers: int | None, threads: int | None,
             limit: int | None = None) -> dict[str, tuple[list[str], Path]]:
    py = [sys.executable]
    extra = ((["--workers", str(workers)] if workers else []) + (["--threads", str(threads)] if threads else [])
             + (["--limit", str(limit)] if limit else []))
    out = HERE / "out" / preset
    return {
        "replay": (py + ["replay.py", "--preset", preset] + extra, HERE),
        "gomem": (["go", "run", "-tags", "cachereplay", "./research/cachereplay/gocheck", "mem", "-lengths",
                   str(HERE / "data" / f"lengths_{preset}.csv")], REPO),
        "precision": (py + ["precision.py", "--preset", preset]
                      + (["--threads", str(threads)] if threads else []), HERE),
        "oldcheck": (py + ["oldcheck.py", "--preset", preset], HERE),
        "fit": (py + ["fit.py", "--run", str(out)], HERE),
    }


def run_stage(name: str, cmd: list[str], cwd: Path, out: Path) -> float:
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if name == "gomem":
        if shutil.which("go") is None:
            raise SystemExit("gomem: `go` is not on PATH; install Go or copy a gomem.json into " + str(out))
        with (out / "gomem.json").open("w", encoding="utf-8") as stdout, \
                (logs / "gomem.log").open("w", encoding="utf-8") as stderr:
            subprocess.run(cmd, cwd=cwd, stdout=stdout, stderr=stderr, check=True)
    else:
        with (logs / f"{name}.log").open("w", encoding="utf-8") as log:
            subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True)
    return time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=("smoke", "full"), default="smoke")
    ap.add_argument("--workers", type=int, help="replay processes (default: the preset's)")
    ap.add_argument("--threads", type=int, help="encoder threads (default: the preset's)")
    ap.add_argument("--limit", type=int, help="replay only the first N first turns (default: all)")
    ap.add_argument("--skip", default="", help=f"comma list of stages to skip, from {','.join(STAGES)}")
    args = ap.parse_args()
    skip = {s for s in args.skip.split(",") if s}
    if skip - set(STAGES):
        raise SystemExit(f"unknown stages {sorted(skip - set(STAGES))}")
    out = HERE / "out" / args.preset
    for name, (cmd, cwd) in commands(args.preset, args.workers, args.threads, args.limit).items():
        if name in skip:
            print(f"{name:9s} skipped", flush=True)
            continue
        print(f"{name:9s} running ({' '.join(cmd[1:4])} ...)", flush=True)
        try:
            seconds = run_stage(name, cmd, cwd, out)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"{name} failed (exit {exc.returncode}); see {out / 'logs' / (name + '.log')}")
        print(f"{name:9s} done in {seconds / 60:.1f} min", flush=True)
    print(f"outputs in {out}")


if __name__ == "__main__":
    main()
