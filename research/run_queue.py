"""Programme 2's CPU campaigns, one after another, unattended.

The testbed and these campaigns cannot share the laptop: a campaign's load
would change what the testbed measures. The queue waits until a given
process has exited (the post-WP11 chain, testbed/hvpa/after_wp11.py), then
runs, in this order:

  wp9-B-own        WP9's full tuning protocol, plant B, own mix, budget
                   enforced, with the WP1 reference lines
                   (research/comparators/full/)
  wp9-A-own        the same for plant A
  wp9-B-own-plant  plant B with --budget plant (only the plant's own
                   admission gate): the budget-symmetry choice of WP5's curve
  wp9-A-own-plant  the same for plant A
  speccurve-runs   the evaluation runs WP5's specification curve needs beyond
                   WP9's (research/speccurve/full_curve.py run)
  speccurve        the curve, every arm re-selected per specification
                   (research/speccurve/FULL_SPEC_CURVE.md)
  morris-r20       WP5's Morris screen as the plan sets it: 20 trajectories,
                   common random numbers (research/sensitivity/MORRIS_r20_crn.md)
  sobol            WP5's Sobol indices on that screen's six leading factors,
                   N = 128 (research/sensitivity/SOBOL.md)
  wp9-B-native     WP9, plant B, native (real-arrival) mix
  wp9-A-native     WP9, plant A, native mix

WP9 comes first because its tuned comparators feed the registration (WP13);
the specification curve then decides how RQ1 can be stated; WP5's
sensitivity indices are a robustness report. Each campaign resumes from its own
journal or partial file, so the queue can be stopped for testbed work and
started again (`--start` names the first campaign) at the cost of the runs in
flight; a finished campaign that is run again only rebuilds its report. A
campaign that fails is logged and the next one starts; one that finishes is
committed (locally). Log: research/out/run_queue.log.

    python run_queue.py --after-pid 2656      # detached, under tools/keepawake.py
    python run_queue.py --list
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LOG = HERE / "out" / "run_queue.log"
WORKERS = "6"


@dataclass(frozen=True)
class Campaign:
    name: str
    cwd: Path
    args: tuple[str, ...]
    outputs: tuple[str, ...]   # repository-relative globs committed when the campaign ends well
    message: str
    ok: tuple[int, ...] = (0,)  # exit codes after which the outputs are a result


def wp9(plant: str, mix: str, budget: str = "enforced") -> Campaign:
    d = f"research/comparators/full/plant{plant}_{mix}_large_s120_{budget}_*"
    return Campaign(
        f"wp9-{plant}-{mix}" + ("" if budget == "enforced" else f"-{budget}"), HERE / "comparators",
        ("tune.py", "--plant", plant, "--mix", mix, "--budget", budget, "--references", "--workers", WORKERS,
         "--out", "full"),
        tuple(f"{d}/{f}" for f in ("tuned.json", "report.md", "runs.csv.gz", "references.csv.gz")),
        f"results(comparators): WP9 full tuning protocol, plant {plant}, {mix} mix, budget {budget}\n\n"
        "5 arms x 64 configurations x 120 tuning workloads, selection overall and per class, the selected\n"
        "configurations on the 120 evaluation workloads, WP1's reference lines. Exploratory until registered.",
        ok=(0, 1))   # tune.py exits 1 when some runs failed: excluded from selection, listed in the report


CAMPAIGNS = (
    wp9("B", "own"),
    wp9("A", "own"),
    wp9("B", "own", "plant"),
    wp9("A", "own", "plant"),
    Campaign("speccurve-runs", HERE / "speccurve", ("full_curve.py", "run", "--workers", WORKERS),
             ("research/speccurve/full/*.eval.csv.gz",),
             "results(speccurve): the evaluation runs the re-selected specifications need beyond WP9's"),
    Campaign("speccurve", HERE / "speccurve", ("full_curve.py", "curve"),
             ("research/speccurve/FULL_SPEC_CURVE.md", "research/speccurve/full_spec_curve.png",
              "research/speccurve/full/curve_effects.csv.gz"),
             "results(speccurve): the plan's specification curve, every arm re-selected per specification"),
    Campaign("morris-r20", HERE / "sensitivity", ("morris_pilot.py", "--trajectories", "20", "--crn",
                                                  "--workers", WORKERS),
             ("research/sensitivity/MORRIS_r20_crn.md", "research/sensitivity/data/morris_runs_r20_crn.csv.gz",
              "research/sensitivity/data/morris_design_r20_crn.json"),
             "results(sensitivity): WP5 Morris screen, 20 trajectories, common random numbers\n\n"
             "The plan's screen: 12 mode-B factors, 260 plants x 20 workloads x joint and per-knob planners."),
    Campaign("sobol", HERE / "sensitivity", ("sobol.py", "--workers", WORKERS),
             ("research/sensitivity/SOBOL.md", "research/sensitivity/data/sobol_runs.csv.gz"),
             "results(sensitivity): WP5 Sobol indices of the coordination gain\n\n"
             "The six leading factors of the r20 screen, Saltelli N = 128, common random numbers."),
    wp9("B", "native"),
    wp9("A", "native"),
    Campaign("wp17-pilot", REPO / "testbed" / "hvpa", ("run_pilot.py",),
             ("research/hvpa/PILOT_LIVE.md", "testbed/hvpa/pilot_runs"),
             "results(hvpa): WP17 live pilot, ABBA pairs per regime and contrast, sigma_d and the pairs needed\n\n"
             "Run last (plan priority: Parts C/D before Part I), on the committed predictions; run_pilot.py\n"
             "refuses predictions that are uncommitted or not sane."),
)


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def wait_for_exit(pid: int) -> None:
    """Block until the process has exited. On Windows the handle is opened at
    once, so a later process that reuses the number is not waited for."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if not handle:
            return                                              # already gone
        try:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)    # INFINITE
        finally:
            kernel32.CloseHandle(handle)
        return
    while True:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(30)


def expand(globs: tuple[str, ...], repo: Path = REPO) -> list[str]:
    """The repository-relative files the globs match now."""
    return sorted({p.relative_to(repo).as_posix() for g in globs for p in repo.glob(g) if p.is_file()})


def commit(c: Campaign, repo: Path = REPO, note: str = "") -> None:
    files = expand(c.outputs, repo)
    if not files:
        log(f"{c.name}: no output to commit")
        return
    subprocess.run(["git", "add", "--", *files], cwd=repo, check=True)
    if subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=repo) != 0:
        subprocess.run(["git", "commit", "-q", "-m", c.message + note], cwd=repo, check=True)
        log(f"{c.name}: committed {len(files)} file(s)")
    else:
        log(f"{c.name}: outputs unchanged, nothing to commit")


def run(c: Campaign) -> int:
    log(f"{c.name}: start (cd {c.cwd.relative_to(REPO).as_posix()} && python {' '.join(c.args)})")
    t0 = time.time()
    with LOG.open("a", encoding="utf-8") as f:
        rc = subprocess.call([sys.executable, *c.args], cwd=c.cwd, stdout=f, stderr=subprocess.STDOUT)
    log(f"{c.name}: exit {rc} after {(time.time() - t0) / 3600:.1f} h")
    if rc in c.ok:
        try:
            commit(c, note="" if rc == 0 else f"\n\nExit {rc}: some runs failed; the report lists them.")
        except subprocess.CalledProcessError as err:
            log(f"{c.name}: commit failed ({err}); the outputs stay in the working tree")
    return rc


def selected(start: str | None) -> list[Campaign]:
    names = [c.name for c in CAMPAIGNS]
    if start is None:
        return list(CAMPAIGNS)
    if start not in names:
        raise SystemExit(f"no campaign {start!r}; campaigns: {', '.join(names)}")
    return list(CAMPAIGNS[names.index(start):])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--after-pid", type=int, default=None, help="wait for this process to exit first")
    ap.add_argument("--start", default=None, help="the first campaign to run (default: the first)")
    ap.add_argument("--list", action="store_true", help="print the campaigns and run nothing")
    args = ap.parse_args(argv)
    todo = selected(args.start)
    if args.list:
        for c in todo:
            print(f"{c.name}: (cd {c.cwd.relative_to(REPO).as_posix()} && python {' '.join(c.args)})")
        return 0
    if args.after_pid is not None:
        log(f"waiting for process {args.after_pid} to exit")
        wait_for_exit(args.after_pid)
    failed = [c.name for c in todo if run(c) not in c.ok]
    log("queue done" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
