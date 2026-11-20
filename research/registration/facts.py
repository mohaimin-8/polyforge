"""What the OSF registration records for commit C, read from the pushed tag (PROTOCOL_NOTES.md,
freezing steps 4 to 6, in one command).

Checks that the tag names a commit holding the protocol and a final design.json (not draft), then
prints C (the full hash), D (SHA-256 of PROTOCOL.md as committed in C, whatever the working copy's
line ends), T0 (the creation time of C's first GitHub run: GitHub's record of the push) and the
drand quicknet round due first at or after T0 + 24 h, with the time left to register before it.

    python research/registration/facts.py --tag wp14-c                          # T0 from `gh run list`
    python research/registration/facts.py --tag wp14-c --t0 2026-11-21T08:00:00Z   # T0 by hand
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import seeds  # noqa: E402

PROTOCOL = "research/registration/PROTOCOL.md"
DESIGN = "research/registration/design.json"
Runner = Callable[..., object]


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


def committed(repo: Path, commit: str, path: str) -> bytes:
    try:
        return git(repo, "show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        raise SystemExit(f"{path} is not in commit {commit[:12]}") from None


def first_run(commit: str, repo: Path, runner: Runner) -> str:
    """The creation time of the commit's first GitHub run (every workflow, branch and tag pushes)."""
    out = runner(["gh", "run", "list", "--commit", commit, "--limit", "100", "--json", "createdAt"],
                 cwd=repo, capture_output=True, text=True, check=True)
    times = sorted(r["createdAt"] for r in json.loads(out.stdout))
    if not times:
        raise SystemExit(f"GitHub has no run for {commit[:12]} yet: push the branch and the tag, then ask again")
    return times[0]


def facts(tag: str, t0: str | None = None, repo: Path = REPO, runner: Runner = subprocess.run,
          fetch: seeds.Fetch | None = None) -> dict:
    try:
        c = git(repo, "rev-parse", f"{tag}^{{commit}}").decode().strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f"no tag {tag!r} here: tag C first (git tag {tag})") from None
    if json.loads(committed(repo, c, DESIGN)).get("draft", True):
        raise SystemExit(f"design.json in {c[:12]} is still marked draft: that commit is not C")
    d = hashlib.sha256(committed(repo, c, PROTOCOL)).hexdigest()
    t0 = t0 or first_run(c, repo, runner)
    info = seeds.chain_info(fetch=fetch)
    r = seeds.round_at_or_after(seeds.parse_time(t0) + seeds.DELAY_S, info["genesis_time"], info["period"])
    return {"C": c, "D": d, "T0": t0, "round": r,
            "round_due": seeds.round_time(r, info["genesis_time"], info["period"]),
            "round_due_utc": seeds.utc(seeds.round_time(r, info["genesis_time"], info["period"]))}


def main(argv: list[str] | None = None, repo: Path = REPO, fetch: seeds.Fetch | None = None,
         now: float | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", required=True, help="the tag at C, pushed with the branch")
    ap.add_argument("--t0", help="T0 by hand (ISO 8601 with Z); default: C's first GitHub run")
    args = ap.parse_args(argv)
    try:
        f = facts(args.tag, args.t0, repo, fetch=fetch)
    except (seeds.DrawError, seeds.NotYet) as exc:
        print(f"no chain info: {exc}", file=sys.stderr)
        return 3
    left = (f["round_due"] - (time.time() if now is None else now)) / 3600
    print(f"C      {f['C']}\nD      {f['D']}\nT0     {f['T0']}\n"
          f"round  {f['round']}, due {f['round_due_utc']} (drand quicknet, chain {seeds.QUICKNET[:12]}...)\n"
          + (f"Register on OSF within {left:.1f} h, before the round is due." if left > 0 else
             "The round is already due: it can no longer be named in advance."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
