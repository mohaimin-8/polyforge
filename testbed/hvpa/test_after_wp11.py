"""The post-WP11 chain starts only on a finished WP11 and never on a stopped one."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import after_wp11  # noqa: E402


def test_state_follows_the_wp11_log(tmp_path, monkeypatch):
    log = tmp_path / "after_calibration.log"
    monkeypatch.setattr(after_wp11, "WP11_LOG", log)
    assert after_wp11.wp11_state() == "running"
    log.write_text("2026-11-14 21:00:00 waiting for the calibration curves\n2026-11-14 21:01:00 + python run_cells.py\n")
    assert after_wp11.wp11_state() == "running"
    log.write_text(log.read_text() + "2026-11-15 11:00:00 done\n")
    assert after_wp11.wp11_state() == "done"
    log.write_text("2026-11-14 21:00:00 + python predict.py\n2026-11-14 21:00:05 exit 1: stopping\n")
    assert after_wp11.wp11_state() == "failed"
    log.write_text("2026-11-14 21:00:00 mid_r1 lost 3.0% of its requests: not a measurement; stopping\n")
    assert after_wp11.wp11_state() == "failed"


def test_run_data_is_kept_without_the_progress_log(tmp_path, monkeypatch):
    monkeypatch.setattr(after_wp11, "LOG", tmp_path / "log.txt")
    src, dst = tmp_path / "out", tmp_path / "runs"
    done = src / "cell03" / "run1"
    done.mkdir(parents=True)
    for f in ("k6-samples.csv.gz", "k6-summary.json", "check.json", "run.json", "k6.log"):
        (done / f).write_text("x")
    (src / "cell04" / "run0").mkdir(parents=True)          # unfinished: no run.json
    (src / "actuator.jsonl").write_text("{}")
    assert after_wp11.keep_wp11_runs(src, dst) == 1
    kept = sorted(p.name for p in (dst / "cell03" / "run1").iterdir())
    assert kept == ["check.json", "k6-samples.csv.gz", "k6-summary.json", "run.json"]
    assert not (dst / "cell04").exists() and (dst / "actuator.jsonl").exists()


def test_the_wp11b_trigger_reads_the_primary_attainment_verdict(tmp_path):
    report = tmp_path / "VALIDATION.md"
    assert after_wp11.wp11_primary_met(report) is None
    table = ("## Primary prediction (cache hits replayed)\n\n| metric | cells | x | y | z | target met |\n"
             "|---|---:|---:|---:|---|---|\n| attainment | 20 | 41.0% | +0.55 | +0.2 [0, 1] | {v} |\n"
             "| cost | 20 | 0.0% | +1.00 | +0 [0, 0] | yes |\n\n## Declared secondary: steady-state cache curve\n\n"
             "| attainment | 20 | 5.0% | +0.90 | +0 [0, 0] | yes |\n")
    report.write_text(table.format(v="NO"))
    assert after_wp11.wp11_primary_met(report) is False      # the secondary's 'yes' does not count
    report.write_text(table.format(v="yes"))
    assert after_wp11.wp11_primary_met(report) is True
