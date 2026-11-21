"""What the OSF registration records for commit C, read from the pushed tag (PROTOCOL_NOTES.md,
freezing steps 4 to 6, in one command).

Checks that the tag names a commit holding the protocol and a final design.json (not draft), then
prints C (the full hash), D (SHA-256 of PROTOCOL.md as committed in C, whatever the working copy's
line ends), T0 (the creation time of C's first GitHub run: GitHub's record of the push) and the
drand quicknet round due first at or after T0 + 24 h, with the time left to register before it.

    python research/registration/facts.py --tag wp14-c                          # T0 from `gh run list`
    python research/registration/facts.py --tag wp14-c --t0 2026-11-21T08:00:00Z   # T0 by hand
    python research/registration/facts.py --tag wp17-c --protocol research/registration/PROTOCOL_H8.md \
        --design testbed/hvpa/main_design.json                                   # a second protocol (H8)
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


class NoT0(RuntimeError):
    """GitHub's record of the commit's runs could not be read, or holds no run yet: no T0."""


def first_run(commit: str, repo: Path, runner: Runner) -> str:
    """T0: the creation time of the commit's first GitHub run (every workflow, branch and tag push).

    confirm.py's draw reads it the same way in CI. gh pages through the runs up to --limit, newest
    first, so the limit is far above any commit's count of runs (157 for the whole repository on
    2026-11-21). Deleting the first run would move T0."""
    try:
        out = runner(["gh", "run", "list", "--commit", commit, "--limit", "1000", "--json", "createdAt"],
                     cwd=repo, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        raise NoT0(f"gh could not list the runs of {commit[:12]} ({detail})") from None
    times = [r["createdAt"] for r in json.loads(out.stdout)]
    if not times:
        raise NoT0(f"GitHub has no run of {commit[:12]} yet")
    return min(times, key=seeds.parse_time)


def facts(tag: str, t0: str | None = None, repo: Path = REPO, runner: Runner = subprocess.run,
          fetch: seeds.Fetch | None = None, protocol: str = PROTOCOL, design: str = DESIGN) -> dict:
    """C, D, T0 and the round for the protocol and design file named (repository paths; WP14's by
    default, H8's are PROTOCOL_H8.md and testbed/hvpa/main_design.json)."""
    try:
        c = git(repo, "rev-parse", f"{tag}^{{commit}}").decode().strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f"no tag {tag!r} here: tag C first (git tag {tag})") from None
    try:
        values = json.loads(committed(repo, c, design))
    except ValueError:                  # not UTF-8, or not JSON
        values = None
    if not isinstance(values, dict):
        raise SystemExit(f"{design} in {c[:12]} is not a JSON object")
    if values.get("draft", True):
        raise SystemExit(f"{Path(design).name} in {c[:12]} is still marked draft: that commit is not C")
    d = hashlib.sha256(committed(repo, c, protocol)).hexdigest()
    t0 = t0 or first_run(c, repo, runner)
    info = seeds.chain_info(fetch=fetch)
    r = seeds.round_at_or_after(seeds.parse_time(t0) + seeds.DELAY_S, info["genesis_time"], info["period"])
    return {"C": c, "D": d, "T0": t0, "round": r,
            "round_due": seeds.round_time(r, info["genesis_time"], info["period"]),
            "round_due_utc": seeds.utc(seeds.round_time(r, info["genesis_time"], info["period"]))}


def main(argv: list[str] | None = None, repo: Path = REPO, fetch: seeds.Fetch | None = None,
         now: float | None = None, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", required=True, help="the tag at C, pushed with the branch")
    ap.add_argument("--t0", help="T0 by hand (ISO 8601 with Z); default: C's first GitHub run")
    ap.add_argument("--protocol", default=PROTOCOL, help=f"the protocol, a repository path (default {PROTOCOL})")
    ap.add_argument("--design", default=DESIGN, help=f"its design file, a repository path (default {DESIGN})")
    args = ap.parse_args(argv)
    if args.t0 is not None:
        try:
            seeds.parse_time(args.t0)
        except ValueError as exc:   # not ISO 8601, or without its UTC offset
            print(f"T0 {args.t0!r}: {exc}; write it as GitHub shows it, in UTC with Z, e.g. 2026-11-21T08:00:03Z",
                  file=sys.stderr)
            return 2
    try:
        f = facts(args.tag, args.t0, repo, runner=runner, fetch=fetch, protocol=args.protocol, design=args.design)
    except NoT0 as exc:
        print(f"{exc}: push the branch and the tag first and log in to gh (gh auth login), or give T0 by "
              "hand with --t0, the creation time on the first run's page", file=sys.stderr)
        return 2
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
