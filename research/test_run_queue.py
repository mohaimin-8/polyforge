"""The CPU campaign queue: its order, --start, the outputs it commits, and
which exit codes count as a result."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_queue as rq  # noqa: E402


def test_the_smoke_then_wp9_run_first_and_start_skips_to_a_named_campaign():
    names = [c.name for c in rq.CAMPAIGNS]
    assert names == ["wp17-smoke", "wp9-B-own", "wp9-A-own", "wp9-B-own-plant", "wp9-A-own-plant",
                     "speccurve-runs", "speccurve", "morris-r20", "sobol", "wp9-B-native", "wp9-A-native",
                     "wp17-pilot", "reproduce"]
    smoke = rq.CAMPAIGNS[0]
    assert (smoke.cwd / smoke.args[0]).exists() and smoke.ok == (0, 1)
    assert [c.name for c in rq.selected("sobol")] == ["sobol", "wp9-B-native", "wp9-A-native", "wp17-pilot",
                                                      "reproduce"]
    pilot, last = rq.CAMPAIGNS[-2], rq.CAMPAIGNS[-1]
    assert (pilot.cwd / pilot.args[0]).exists() and pilot.ok == (0,)
    assert (last.cwd / last.args[0]).exists() and last.outputs == ()
    assert "--budget" in rq.wp9("B", "own", "plant").args and rq.wp9("B", "own", "plant").args[
        rq.wp9("B", "own", "plant").args.index("--budget") + 1] == "plant"
    assert rq.selected(None) == list(rq.CAMPAIGNS)
    with pytest.raises(SystemExit):
        rq.selected("nope")


def test_only_runs_the_named_campaigns_in_queue_order():
    names = [c.name for c in rq.selected(None, ["reproduce", "wp17-pilot", "wp9-B-own-plant"])]
    assert names == ["wp9-B-own-plant", "wp17-pilot", "reproduce"]
    assert [c.name for c in rq.selected("wp17-pilot", ["wp9-B-own-plant", "reproduce"])] == ["reproduce"]
    with pytest.raises(SystemExit, match="nope"):
        rq.selected(None, ["wp17-pilot", "nope"])


def test_outputs_expand_to_the_files_the_campaign_wrote_and_never_its_journal(tmp_path):
    d = tmp_path / "research" / "comparators" / "full" / "plantB_own_large_s120_enforced_abcd1234"
    d.mkdir(parents=True)
    for f in ("tuned.json", "report.md", "runs.csv.gz", "journal.jsonl"):
        (d / f).write_text("x")
    rel = "research/comparators/full/plantB_own_large_s120_enforced_abcd1234/"
    assert rq.expand(rq.wp9("B", "own").outputs, tmp_path) == [rel + f for f in ("report.md", "runs.csv.gz",
                                                                                 "tuned.json")]
    assert rq.expand(rq.wp9("B", "native").outputs, tmp_path) == []          # another mix's directory
    assert rq.expand(rq.wp9("B", "own", "plant").outputs, tmp_path) == []    # another budget's directory


def test_a_wp9_exit_1_is_a_result_and_a_crash_is_not(monkeypatch, tmp_path):
    monkeypatch.setattr(rq, "LOG", tmp_path / "log.txt")
    committed = []
    monkeypatch.setattr(rq, "commit", lambda c, repo=None, note="": committed.append((c.name, note)))
    morris = next(c for c in rq.CAMPAIGNS if c.name == "morris-r20")
    for rc in (0, 1, 2):
        monkeypatch.setattr(rq.subprocess, "call", lambda *a, rc=rc, **k: rc)
        assert rq.run(rq.wp9("B", "own")) == rc and rq.run(morris) == rc
    assert committed == [("wp9-B-own", ""), ("morris-r20", ""),
                         ("wp9-B-own", "\n\nExit 1: some runs failed; the report lists them.")]


def test_waiting_for_a_process_that_is_gone_returns_at_once():
    rq.wait_for_exit(2 ** 22 + 7)   # no such process
