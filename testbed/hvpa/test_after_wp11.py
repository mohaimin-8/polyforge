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
