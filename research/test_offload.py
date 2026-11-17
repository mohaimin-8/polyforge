"""The CI split of the CPU campaigns: a split campaign writes what one run of
it writes, every run is in exactly one shard, and only a campaign's outputs
are committed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import offload  # noqa: E402
import run_queue  # noqa: E402

COMPARATORS = Path(__file__).resolve().parent / "comparators"
TINY = ("--plant", "B", "--arms", "joint", "rules", "--configs", "2", "--tuning-workloads", "2",
        "--evaluation-workloads", "2", "--steps", "8", "--workers", "1")

FAKE = '''import pathlib
from multiprocessing import Pool

import pandas as pd

OUT = pathlib.Path(__file__).resolve().parent / "data"


class Run:
    def __init__(self, system, workload):
        self.system, self.workload = system, workload


def run_one(job):
    pid, values, run = job
    return {"plant": pid, "system": run.system, "workload": run.workload, "J": pid / 3 + len(run.workload) / 7,
            "wall_s": 0.0, **values}


def main():
    OUT.mkdir(exist_ok=True)
    partial = OUT / "fake.partial.csv"
    todo = [(p, {"x": p / 9, "y": 0.1 * p}, Run(s, w)) for p in range(5) for s in ("a", "b") for w in ("w1", "w22")]
    with Pool(2) as pool:
        for row in pool.imap_unordered(run_one, todo, chunksize=1):
            pd.DataFrame([row]).to_csv(partial, mode="a", header=not partial.exists(), index=False)
    runs = pd.read_csv(partial)
    if len(runs) != len(todo):
        raise RuntimeError("partial")
    runs.sort_values(["plant", "system", "workload"]).to_csv(OUT / "fake_runs.csv", index=False)
    return 0
'''


def test_the_cpu_campaigns_split_and_the_live_ones_do_not():
    for name in offload.SHARDS:
        assert offload.splittable(name).name == name and 1 <= offload.SHARDS[name] <= offload.MAX_SHARDS
    for name in ("wp17-smoke", "wp17-pilot", "reproduce", "speccurve"):
        with pytest.raises(SystemExit, match="not a CPU campaign"):
            offload.splittable(name)
    with pytest.raises(SystemExit, match="shards must be"):
        offload.plan("morris-r20", 0)
    with pytest.raises(SystemExit, match="shards must be"):
        offload.plan("morris-r20", offload.MAX_SHARDS + 1)
    argv = offload.argv_of(offload.queue_campaign("wp9-A-own-plant"), 4)
    assert argv[argv.index("--workers") + 1] == "4" and argv[argv.index("--budget") + 1] == "plant"
    argv = offload.argv_of(offload.queue_campaign("morris-r20"), 4)
    assert argv[argv.index("--workers") + 1] == "4" and "--crn" in argv


def tiny(out: Path) -> run_queue.Campaign:
    return run_queue.Campaign("tiny", COMPARATORS, ("tune.py", *TINY, "--out", str(out)), (), "tiny")


def outputs(root: Path) -> tuple[dict, pd.DataFrame, str]:
    (tag,) = [d for d in root.iterdir() if d.is_dir()]
    tuned = json.loads((tag / "tuned.json").read_text(encoding="utf-8"))
    runs = pd.read_csv(tag / "runs.csv.gz", float_precision="round_trip").drop(columns="wall_s")
    return tuned, runs.sort_values(["phase", "arm", "config", "workload"]).reset_index(drop=True), \
        (tag / "report.md").read_text(encoding="utf-8")


def test_a_split_tune_campaign_writes_what_one_run_writes(tmp_path):
    one, split = tiny(tmp_path / "one"), tiny(tmp_path / "split")
    with offload.inside(one):
        import tune

        assert tune.main(list(one.args[1:])) == 0
    with offload.inside(split):
        stages = offload.journal_stages(split)
    assert len(stages) == 1 and len(stages[0].jobs) == 2 * 2 * 2 and not stages[0].journal.exists()
    for i in range(3):
        with offload.inside(split):
            offload._run_journal_shard(stages, i, 3, tmp_path / "shards" / f"shard-{i}", 1)
    with offload.inside(split):
        offload.merge_journals(stages, tmp_path / "shards")
        assert tune.main(list(split.args[1:])) == 0
    a, b = outputs(tmp_path / "one"), outputs(tmp_path / "split")
    assert a[0] == b[0]
    pd.testing.assert_frame_equal(a[1], b[1])
    assert a[2] == b[2]


def test_every_run_is_merged_exactly_once(tmp_path):
    jobs = tuple({"phase": "tuning", "arm": "joint", "config": c, "workload": w} for c in (0, 1) for w in ("p1", "p2"))
    stage = offload.Stage(tmp_path / "out" / "journal.jsonl", jobs)
    shards = tmp_path / "shards"
    (shards / "shard-0").mkdir(parents=True)
    rows = [json.dumps({**j, "J": 1.0}) for j in jobs]
    (shards / "shard-0" / "journal.jsonl").write_text("\n".join(rows[:3]) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="1 of the campaign's runs missing"):
        offload.merge_journals([stage], shards)
    (shards / "shard-1").mkdir()
    (shards / "shard-1" / "journal.jsonl").write_text(rows[3] + "\n" + rows[0] + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="twice"):
        offload.merge_journals([stage], shards)
    (shards / "shard-1" / "journal.jsonl").write_text(rows[3] + "\n", encoding="utf-8")
    offload.merge_journals([stage], shards)
    assert stage.journal.read_text(encoding="utf-8").splitlines() == rows
    with pytest.raises(SystemExit, match="exists"):
        offload.merge_journals([stage], shards)


def fake_campaign(root: Path, name: str) -> run_queue.Campaign:
    root.mkdir()
    (root / f"{name}.py").write_text(FAKE, encoding="utf-8")
    return run_queue.Campaign(name, root, (f"{name}.py",), (), name)


def test_a_split_pool_campaign_writes_what_one_run_writes(tmp_path):
    one, split = fake_campaign(tmp_path / "one", "fakepool_one"), fake_campaign(tmp_path / "split", "fakepool_split")
    with offload.inside(one):
        assert __import__("fakepool_one").main() == 0
    with offload.inside(split):
        fn, jobs = offload.pool_jobs(split)
    assert len(jobs) == 20
    for i in range(3):
        (tmp_path / "shards" / f"shard-{i}").mkdir(parents=True)
        offload._run_pool_shard(fn, jobs[i::3], tmp_path / "shards" / f"shard-{i}" / offload.SHARD_ROWS, 1)
    with offload.inside(split):
        assert offload._finalize_pool(split, tmp_path / "shards", 1) == 0
    assert (tmp_path / "one" / "data" / "fake_runs.csv").read_bytes() == \
        (tmp_path / "split" / "data" / "fake_runs.csv").read_bytes()
    first = (tmp_path / "shards" / "shard-0" / offload.SHARD_ROWS)
    first.write_text(first.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")   # rows lost
    with offload.inside(split):
        with pytest.raises(SystemExit, match="missing"):
            offload._finalize_pool(split, tmp_path / "shards", 1)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def test_collect_commits_only_the_campaigns_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(run_queue, "LOG", tmp_path / "queue.log")
    repo, art = tmp_path / "repo", tmp_path / "artifact"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.org")
    git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x\n", encoding="utf-8")
    git(repo, "add", "README")
    git(repo, "commit", "-q", "-m", "init")
    files = ["research/sensitivity/MORRIS_r20_crn.md", "research/sensitivity/data/morris_runs_r20_crn.csv.gz"]
    for rel in files:
        (art / rel).parent.mkdir(parents=True, exist_ok=True)
        (art / rel).write_text(rel, encoding="utf-8")
    prov = {"campaign": "morris-r20", "exit": 0, "files": files, "commit": "a" * 40, "shards": 10,
            "platform": "Linux", "python": "3.13.5"}
    (art / offload.PROVENANCE).mkdir()
    (art / offload.PROVENANCE / "morris-r20.json").write_text(json.dumps(prov), encoding="utf-8")
    assert offload.collect("morris-r20", art, "https://example.org/run/1", repo) == files
    log = git(repo, "log", "-1", "--format=%B")
    assert log.startswith("results(sensitivity): WP5 Morris screen") and "https://example.org/run/1" in log
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split()) == sorted(files)
    (art / files[0]).write_text("other", encoding="utf-8")
    with pytest.raises(SystemExit, match="other contents"):
        offload.collect("morris-r20", art, "https://example.org/run/2", repo)
    prov["files"] = ["research/sensitivity/SOBOL.md"]
    (art / offload.PROVENANCE / "morris-r20.json").write_text(json.dumps(prov), encoding="utf-8")
    with pytest.raises(SystemExit, match="not an output"):
        offload.collect("morris-r20", art, "https://example.org/run/3", repo)
