"""Programme 2's CPU campaigns on GitHub Actions, split into shards.

The live testbed needs the laptop to itself and the laptop runs one campaign
at a time, so the CPU campaigns of research/run_queue.py can also run on CI
(.github/workflows/campaign.yml, started by pushing a branch named
offload/<campaign>). The same scripts with the same arguments (only --workers
follows the runner), split over up to 64 jobs of under six hours each:

  plan        the campaign's runs; prints the shard list (JSON) for the matrix
  run         shard i of N: every N-th run of the campaign's first stage, in the
              campaign's own row format (tune.py's journal; the rows
              morris_pilot.py and sobol.py append to their partial file)
  finalize    the shards' rows checked against the campaign's runs (each
              exactly once), then the campaign's own command on them: tune.py
              and full_curve.py resume from the merged journal and run what is
              left (tune.py's evaluation and reference runs); morris_pilot.py
              and sobol.py are handed the rows in place of a worker pool. The
              outputs the queue commits are copied to --stage under their
              repository paths, with a provenance file
  collect     on the laptop: a downloaded "outputs" artifact copied into the
              tree and committed as the queue commits a campaign, the CI run
              named in the message
  crosscheck  committed WP9 evaluation runs run again here: the same numbers on
              another platform or not (full_curve.recheck)

    python research/offload.py plan wp9-A-own-plant
    python research/offload.py run wp9-A-own-plant --shard 3 --shards 16 --out shard-3
    python research/offload.py finalize wp9-A-own-plant --shards-dir shards --stage outputs
    python research/offload.py collect wp9-A-own-plant --artifact DIR --run-url URL
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import run_queue  # noqa: E402

MAX_SHARDS = 64
JOURNAL_KINDS = ("tune.py", "full_curve.py")        # resume from a tune.execute journal
POOL_KINDS = ("morris_pilot.py", "sobol.py")         # append their pool's rows to a partial CSV
SHARDS = {"wp9-A-own-plant": 16, "wp9-B-own-plant": 12, "wp9-B-native": 16, "wp9-A-native": 16,
          "morris-r20": 10, "sobol": 32, "speccurve-runs": 4}   # each shard well under 6 h on 4 vCPU
FOLLOW = {"speccurve-runs": ("speccurve",)}          # cheap campaigns run right after, on the same runner
SHARD_ROWS = "runs.partial.csv"
PROVENANCE = "offload"
PACKAGES = ("numpy", "pandas", "scipy", "SALib", "statsmodels", "matplotlib", "PyYAML", "duckdb")


class Captured(Exception):
    """Raised inside a campaign's own main() once its runs are known."""


@dataclass(frozen=True)
class Stage:
    journal: Path    # the file the campaign resumes from
    jobs: tuple      # the campaign's jobs for it, in the campaign's order


def queue_campaign(name: str) -> run_queue.Campaign:
    found = [c for c in run_queue.CAMPAIGNS if c.name == name]
    if not found:
        raise SystemExit(f"no campaign {name!r} in research/run_queue.py")
    return found[0]


def splittable(name: str) -> run_queue.Campaign:
    c = queue_campaign(name)
    if c.args[0] not in JOURNAL_KINDS + POOL_KINDS or (c.args[0] == "full_curve.py" and c.args[1] != "run"):
        raise SystemExit(f"{name}: not a CPU campaign this splits ({' '.join(c.args)})")
    return c


def argv_of(c: run_queue.Campaign, workers: int) -> list[str]:
    """The campaign's arguments with this machine's worker count."""
    args = list(c.args[1:])
    if "--workers" in args:
        args[args.index("--workers") + 1] = str(workers)
    return args


@contextlib.contextmanager
def inside(c: run_queue.Campaign):
    """As the queue runs a campaign: its directory the working directory and
    first on the import path. Paths the campaign adds stay (workers import
    from them); the working directory and sys.argv come back."""
    cwd, argv = os.getcwd(), list(sys.argv)
    os.chdir(c.cwd)
    if str(c.cwd) not in sys.path:
        sys.path.insert(0, str(c.cwd))
    try:
        yield
    finally:
        os.chdir(cwd)
        sys.argv[:] = argv


@contextlib.contextmanager
def patched(obj, name: str, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def journal_stages(c: run_queue.Campaign) -> list[Stage]:
    """The tune.execute calls the campaign's main() makes before it needs a
    result: tune.py's tuning stage (its evaluation follows the selection),
    every campaign of full_curve.py's run step. Call inside(c); nothing runs."""
    script = c.args[0]
    module = importlib.import_module(script[:-3])
    import tune

    stages: list[Stage] = []

    def capture(jobs, workers, label, stall_s=3600.0, journal=None):
        if journal is None:
            raise SystemExit(f"{c.name}: a stage without a journal cannot be split")
        stages.append(Stage(Path(journal).resolve(), tuple(jobs)))
        if script == "tune.py":
            raise Captured
        return []

    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.redirect_stdout(sys.stderr))
        stack.enter_context(patched(tune, "execute", capture))
        if script == "full_curve.py":   # it re-runs WP9 rows first; finalize does, with the real command
            stack.enter_context(patched(module, "recheck", lambda *a, **k: None))
        try:
            module.main(argv_of(c, 1))
        except Captured:
            pass
    names = [s.journal.name for s in stages]
    if len(set(names)) != len(names):
        raise SystemExit(f"{c.name}: two stages share a journal name {names}")
    return stages


def pool_key(job: tuple) -> tuple:
    """A morris_pilot.py or sobol.py run: (plant, system, workload), as their resume does."""
    return job[0], job[2].system, job[2].workload


def pool_jobs(c: run_queue.Campaign) -> tuple:
    """The function and the jobs the campaign's main() hands its pool. Call
    inside(c); nothing runs."""
    module = importlib.import_module(c.args[0][:-3])

    class CapturePool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def imap_unordered(self, fn, jobs, chunksize=1):
            raise Captured(fn, list(jobs))

    sys.argv = [c.args[0], *argv_of(c, 1)]
    with contextlib.redirect_stdout(sys.stderr), patched(module, "Pool", CapturePool):
        try:
            module.main()
        except Captured as got:
            return got.args
    raise SystemExit(f"{c.name}: its main() handed no runs to a pool")


def count_runs(c: run_queue.Campaign) -> int:
    with inside(c):
        if c.args[0] in JOURNAL_KINDS:
            return sum(len(s.jobs) for s in journal_stages(c))
        return len(pool_jobs(c)[1])


def plan(name: str, shards: int | None) -> list[int]:
    c = splittable(name)
    n = SHARDS.get(name, 8) if shards is None else shards
    if not 1 <= n <= MAX_SHARDS:
        raise SystemExit(f"shards must be 1..{MAX_SHARDS}, not {n}")
    runs = count_runs(c)   # (tune.py creates the campaign's output directory: run this on CI, not in a tree)
    print(f"{name}: {runs} runs in the first stage, {n} shards", file=sys.stderr)
    return list(range(n))


def run_shard(name: str, shard: int, shards: int, out: Path, workers: int) -> int:
    """Every `shards`-th run from `shard` on, in the campaign's row format."""
    c = splittable(name)
    if not 0 <= shard < shards <= MAX_SHARDS:
        raise SystemExit(f"shard {shard} is not one of 0..{shards - 1}")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    with inside(c):
        if c.args[0] in JOURNAL_KINDS:
            return _run_journal_shard(journal_stages(c), shard, shards, out, workers)
        fn, jobs = pool_jobs(c)
        _run_pool_shard(fn, jobs[shard::shards], out / SHARD_ROWS, workers)
        return 0


def _run_journal_shard(stages: list[Stage], shard: int, shards: int, out: Path, workers: int) -> int:
    import tune

    mine = [(s.journal.name, job) for s in stages for job in s.jobs][shard::shards]
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in dict.fromkeys(n for n, _ in mine):
        rows = tune.execute([job for n, job in mine if n == name], workers, f"{name} shard", journal=out / name)
        failed += sum(1 for r in rows if r.get("error"))
    print(f"{len(mine)} runs, {failed} failed", file=sys.stderr)
    return 0   # failed runs stay in the journal; finalize runs them again, as tune.py's resume does


def _run_pool_shard(fn, jobs: list, path: Path, workers: int) -> None:
    import pandas as pd

    def note(row: dict) -> None:   # exactly as morris_pilot.py and sobol.py append their rows
        pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)

    if not jobs:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if workers <= 1:
        for job in jobs:
            note(fn(job))
        return
    with Pool(min(workers, len(jobs))) as pool:
        for row in pool.imap_unordered(fn, jobs, chunksize=1):
            note(row)


def merge_journals(stages: list[Stage], shards_dir: Path) -> None:
    """Each stage's journal from the shards' rows: every job exactly once."""
    import tune

    for s in stages:
        want = [tune.job_key(job) for job in s.jobs]
        lines: dict[str, str] = {}
        for f in sorted(shards_dir.glob(f"*/{s.journal.name}")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    key = tune.job_key(json.loads(line))
                    if key in lines:
                        raise SystemExit(f"{s.journal.name}: run {key} appears twice in the shards")
                    lines[key] = line
        missing, extra = set(want) - set(lines), set(lines) - set(want)
        if missing or extra:
            raise SystemExit(f"{s.journal.name}: {len(missing)} of the campaign's runs missing from the shards, "
                             f"{len(extra)} rows that are not the campaign's")
        if s.journal.exists() and s.journal.stat().st_size:
            raise SystemExit(f"{s.journal} exists: finalize starts from a tree without the campaign's journal")
        s.journal.parent.mkdir(parents=True, exist_ok=True)
        s.journal.write_text("".join(lines[k] + "\n" for k in want), encoding="utf-8")


def read_pool_rows(shards_dir: Path) -> dict:
    """The shards' rows by run, floats exactly as written."""
    import pandas as pd

    rows: dict = {}
    for f in sorted(shards_dir.glob(f"*/{SHARD_ROWS}")):
        for row in pd.read_csv(f, float_precision="round_trip").to_dict("records"):
            key = (int(row["plant"]), row["system"], row["workload"])
            if key in rows:
                raise SystemExit(f"run {key} appears twice in the shards")
            rows[key] = row
    return rows


def replay_pool(rows: dict):
    """A pool that hands back the shards' rows instead of running the jobs."""
    class ReplayPool:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def imap_unordered(self, fn, jobs, chunksize=1):
            for job in jobs:
                yield rows.pop(pool_key(job))

    return ReplayPool


def finalize(name: str, shards_dir: Path, stage: Path, workers: int) -> int:
    c = splittable(name)
    with inside(c):
        if c.args[0] in JOURNAL_KINDS:
            merge_journals(journal_stages(c), shards_dir)
            rc = subprocess.call([sys.executable, c.args[0], *argv_of(c, workers)], cwd=c.cwd)
        else:
            rc = _finalize_pool(c, shards_dir, workers)
    print(f"{name}: exit {rc}", file=sys.stderr)
    if rc not in c.ok:
        return rc
    done = {name: rc}
    for follow in FOLLOW.get(name, ()):
        f = queue_campaign(follow)
        done[follow] = subprocess.call([sys.executable, *argv_with_script(f, workers)], cwd=f.cwd)
        if done[follow] not in f.ok:
            return done[follow]
    stage_outputs(done, stage)
    return 0


def argv_with_script(c: run_queue.Campaign, workers: int) -> list[str]:
    return [c.args[0], *argv_of(c, workers)]


def _finalize_pool(c: run_queue.Campaign, shards_dir: Path, workers: int) -> int:
    fn, jobs = pool_jobs(c)
    rows = read_pool_rows(shards_dir)
    want = {pool_key(job) for job in jobs}
    missing, extra = want - set(rows), set(rows) - want
    if missing or extra:
        raise SystemExit(f"{c.name}: {len(missing)} of the campaign's runs missing from the shards, "
                         f"{len(extra)} rows that are not the campaign's")
    module = sys.modules[c.args[0][:-3]]
    sys.argv = argv_with_script(c, workers)
    with patched(module, "Pool", replay_pool(rows)):
        rc = module.main()
    if rows:
        raise SystemExit(f"{c.name}: {len(rows)} rows were not asked for")
    return 0 if rc is None else rc


def matches(c: run_queue.Campaign, rel: str) -> bool:
    return any(fnmatch.fnmatchcase(rel, g) for g in c.outputs)


def stage_outputs(done: dict[str, int], stage: Path) -> None:
    """The outputs the queue would commit, under their repository paths, and
    what produced them."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    for name, rc in done.items():
        c = queue_campaign(name)
        files = run_queue.expand(c.outputs)
        if not files:
            raise SystemExit(f"{name}: no outputs to stage")
        for rel in files:
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dest)
        prov = {"campaign": name, "exit": rc, "files": files, "commit": head,
                "shards": int(os.environ.get("OFFLOAD_SHARDS", "0")) or None,
                "run_url": os.environ.get("OFFLOAD_RUN_URL"), "python": platform.python_version(),
                "platform": platform.platform(), "cpus": os.cpu_count(), "packages": packages()}
        (stage / PROVENANCE).mkdir(parents=True, exist_ok=True)
        (stage / PROVENANCE / f"{name}.json").write_text(json.dumps(prov, indent=1), encoding="utf-8")


def packages() -> dict[str, str | None]:
    out = {}
    for p in PACKAGES:
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = None
    return out


def collect(name: str, artifact: Path, run_url: str, repo: Path = REPO) -> list[str]:
    """Copy a finalize job's outputs into the tree and commit each campaign as
    the queue does. Refuses files that are not the campaign's outputs and
    existing files with other contents."""
    names = [name, *FOLLOW.get(name, ())]
    provs = {n: json.loads((artifact / PROVENANCE / f"{n}.json").read_text(encoding="utf-8")) for n in names}
    copied = []
    for n in names:
        c = queue_campaign(n)
        for rel in provs[n]["files"]:
            if not matches(c, rel):
                raise SystemExit(f"{rel} is not an output of {n}")
            src, dest = artifact / rel, repo / rel
            if dest.exists() and dest.read_bytes() != src.read_bytes():
                raise SystemExit(f"{rel} exists with other contents; not overwritten")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied.append(rel)
    for n in names:
        p = provs[n]
        note = (f"\n\nRun on GitHub Actions ({p['shards']} shards, {p['platform']}, Python {p['python']}): "
                f"{run_url}, commit {p['commit'][:12]}.")
        if p["exit"]:
            note += f"\n\nExit {p['exit']}: some runs failed; the report lists them."
        run_queue.commit(queue_campaign(n), repo, note=note)
    return copied


def crosscheck() -> int:
    """Run one committed WP9 evaluation row per arm again here and require the
    same numbers (full_curve.recheck): the platform does not change a run."""
    c = queue_campaign("speccurve-runs")
    checked = 0
    with inside(c):
        full_curve = importlib.import_module("full_curve")
        for plant in ("A", "B"):
            for budget in ("enforced", "plant"):
                try:
                    camp = full_curve.load(plant, budget)
                except SystemExit:
                    continue   # not committed yet
                arms = tuple(sorted(set(camp.evaluation.arm)))
                full_curve.recheck(camp, arms)
                print(f"{camp.path.name}: {len(arms)} evaluation runs identical ({', '.join(arms)})")
                checked += 1
    return 0 if checked else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("campaign")
    p.add_argument("--shards", type=int, default=None)
    r = sub.add_parser("run")
    r.add_argument("campaign")
    r.add_argument("--shard", type=int, required=True)
    r.add_argument("--shards", type=int, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    f = sub.add_parser("finalize")
    f.add_argument("campaign")
    f.add_argument("--shards-dir", type=Path, required=True)
    f.add_argument("--stage", type=Path, required=True)
    f.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    k = sub.add_parser("collect")
    k.add_argument("campaign")
    k.add_argument("--artifact", type=Path, required=True)
    k.add_argument("--run-url", required=True)
    sub.add_parser("crosscheck")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        print(json.dumps(plan(args.campaign, args.shards)))
        return 0
    if args.cmd == "run":
        return run_shard(args.campaign, args.shard, args.shards, args.out.resolve(), args.workers)
    if args.cmd == "finalize":
        return finalize(args.campaign, args.shards_dir.resolve(), args.stage.resolve(), args.workers)
    if args.cmd == "collect":
        print("\n".join(collect(args.campaign, args.artifact.resolve(), args.run_url)))
        return 0
    return crosscheck()


if __name__ == "__main__":
    sys.exit(main())
