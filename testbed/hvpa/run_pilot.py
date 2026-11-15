"""The WP17 live pilot as the last campaign of the CPU queue (programme 2).

  1. check    research/hvpa/predictions.json and calibration.json are committed
              and unchanged (the predictions come first), and the predictions
              are sane (`problems`): exit 3 before anything runs when not;
  2. cluster  pf-testbed held at zero replicas, every pf-hvpa deployment
              available;
  3. pilot    pilot.py, 6 schedules per regime, each run once by A4, A3 and A1
              (54 one-hour runs for three regimes, about 58 hours), resumable
              from its own records;
  4. keep     PILOT.md as research/hvpa/PILOT_LIVE.md and every run record
              (JSON: arm, target, k6 summary, cost watch, planner decisions; no
              key, no prompt) into testbed/hvpa/pilot_runs/, which the queue
              commits.

    python run_pilot.py
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
REPO = TESTBED.parent
RESEARCH = REPO / "research" / "hvpa"
PREDICTIONS = RESEARCH / "predictions.json"
CALIBRATION = RESEARCH / "calibration.json"
OUT = TESTBED / "out" / "hvpa" / "pilot"
KEEP = HERE / "pilot_runs"
CONTEXT = "kind-polyforge-testbed"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def committed_unchanged(path: Path) -> bool:
    rel = path.relative_to(REPO).as_posix()
    return git("ls-files", "--error-unmatch", rel).returncode == 0 and git("diff", "--quiet", "HEAD", "--", rel).returncode == 0


def problems(pred: dict) -> list[str]:
    """Why the predictions cannot drive the pilot (empty: they can)."""
    out = []
    regimes = {(r.get("width"), r.get("sigma")): r for r in pred.get("regimes", [])}
    live = pred.get("live_regimes", [])
    if not regimes or not live:
        out.append("no regimes or no live regimes")
    for r in regimes.values():
        for key in ("gain_vs_A3", "gain_vs_A1"):
            g = r.get(key)
            if not isinstance(g, (int, float)) or not math.isfinite(g) or g >= 1.0:
                out.append(f"regime w{r.get('width')} s{r.get('sigma')}: {key} {g!r} is not a gain below 1")
    for lr in live:
        w, s = lr.get("width"), lr.get("sigma")
        if (w, s) not in regimes:
            out.append(f"live regime w{w} s{s} is not a predicted regime")
        if not isinstance(w, int) or w < 1:
            out.append(f"live regime width {w!r} is not a positive integer")
        t = pred.get("tuned_targets", {}).get(str(s), {}).get("cpu_default")
        if not isinstance(t, (int, float)) or not 0.0 < t <= 1.0:
            out.append(f"live regime s{s}: tuned default-VPA target {t!r} is not in (0, 1]")
    return out


def kubectl(*args: str) -> None:
    subprocess.run(["kubectl", "--context", CONTEXT, *args], check=True, capture_output=True, text=True)


def keep(src: Path = OUT, dst: Path = KEEP) -> int:
    n = 0
    for rec in sorted(src.rglob("*.json")):
        target = dst / rec.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rec, target)
        n += 1
    if (src / "PILOT.md").exists():
        shutil.copy2(src / "PILOT.md", RESEARCH / "PILOT_LIVE.md")
    return n


def main() -> int:
    for path in (PREDICTIONS, CALIBRATION):
        if not committed_unchanged(path):
            print(f"{path.relative_to(REPO).as_posix()} is not committed as it is on disk: no live run", flush=True)
            return 3
    found = problems(json.loads(PREDICTIONS.read_text(encoding="utf-8")))
    if found:
        print("the predictions cannot drive the pilot:\n  " + "\n  ".join(found), flush=True)
        return 3
    kubectl("-n", "pf-testbed", "scale", "deployment", "--all", "--replicas=0")
    kubectl("-n", "pf-hvpa", "wait", "--for=condition=available", "deployment", "--all", "--timeout=900s")
    rc = subprocess.call([sys.executable, "pilot.py", "--predictions", str(PREDICTIONS), "--calibration",
                          str(CALIBRATION), "--out", str(OUT)], cwd=HERE)
    print(f"pilot.py exit {rc}; kept {keep()} run records", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
