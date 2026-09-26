"""The prereg-timing gate must be able to fail, and must read time correctly.

Audit 2026-09-26: check_preregs.py anchors each protocol against its RECORD's
first commit, never against when its campaign actually RAN -- and the
campaign databases stored run times in the recording host's local time,
unmarked. Two campaigns (eviction parity, order permutation) began running
before their protocol was committed, and no gate could see it.

Run: python -m pytest scripts/test_check_prereg_timing.py -q
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "check_prereg_timing", REPO_ROOT / "scripts" / "check_prereg_timing.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


def _host_facts(evidence: Path, os_name: str) -> None:
    run = evidence / "runs" / "arm__cell__rep0"
    run.mkdir(parents=True)
    (run / "host_facts.json").write_text(json.dumps({"os": os_name}), encoding="utf-8")


def test_new_harness_rows_are_read_as_utc(tmp_path):
    assert ct.host_offset_hours("1.1.0", tmp_path / "none")[0] == 0
    assert ct.host_offset_hours("1.10.0", tmp_path / "none")[0] == 0


def test_old_rows_from_a_cloud_host_are_utc_by_evidence(tmp_path):
    _host_facts(tmp_path / "ev", "Linux 6.8.0-1063-aws")
    hours, basis = ct.host_offset_hours("1.0.0", tmp_path / "ev")
    assert hours == 0 and "host_facts" in basis


def test_old_laptop_rows_take_the_conservative_reading(tmp_path):
    # +6 h makes a run look EARLIER, so an unknown host can raise a false
    # alarm but never hide a run that preceded its protocol.
    assert ct.host_offset_hours("1.0.0", tmp_path / "none")[0] == 6
    _host_facts(tmp_path / "ev", "Windows 11 10.0.26200")
    assert ct.host_offset_hours("1.0.0", tmp_path / "ev")[0] == 6


def test_a_run_before_its_protocol_fails_unless_disclosed():
    assert ct.verdict(-240.0, "matrix_x", {}) == "RUN BEFORE PREREG"
    assert ct.verdict(-240.0, "matrix_x", {"matrix_x": "why"}) == "disclosed"
    assert ct.verdict(30.0, "matrix_x", {}) == "tight"
    assert ct.verdict(7200.0, "matrix_x", {}) == "ok"
    assert ct.verdict(None, "matrix_x", {}) == "no run data"


def test_a_disclosure_that_no_longer_applies_is_reported():
    rows = [{"name": "matrix_x", "verdict": "ok"},
            {"name": "matrix_y", "verdict": "no run data"}]
    # matrix_x ran after its protocol, so its exception is stale; matrix_y
    # cannot be judged on this checkout, so its exception is not.
    assert ct.stale_disclosures(rows, {"matrix_x": "a", "matrix_y": "b"}) == ["matrix_x"]


def test_the_gate_passes_on_the_real_history():
    assert ct.main() == 0
