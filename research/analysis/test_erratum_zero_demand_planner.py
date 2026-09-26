"""The zero-demand erratum is derived from the run tables and verified history."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("ezd", HERE / "erratum_zero_demand_planner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.strip()


def test_cited_first_commits_match_the_history(monkeypatch, tmp_path):
    if _git("rev-parse", "--is-shallow-repository") == "true":
        pytest.skip("shallow clone")
    mod = _mod(monkeypatch, tmp_path)
    for name, (day, sha) in mod.FIRST_COMMIT.items():
        first = _git("log", "--reverse", "--format=%h %ad", "--date=short", "--",
                     f"research/analysis/{name}").splitlines()[0]
        assert first.split()[1] == day, name
        assert first.split()[0].startswith(sha[:7]), name
    fix = _git("show", "-s", "--format=%B", mod.FIX_COMMIT)
    assert "live\nplanner read zero demand" in fix or "planner read zero demand" in fix.replace("\n", " ")


def test_phase7_validity_is_counted_from_the_run_table(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    p7 = mod.phase7()
    assert (p7["valid"], p7["runs"]) == (10, 12)
    assert p7["last"] < mod.FIX_UTC
    assert mod.main() == 0
    text = (tmp_path / mod.RECORD).read_text(encoding="utf-8")
    assert "**10 of 12**" in text and "all before the fix" in text


def test_utc_rows_are_not_shifted(monkeypatch, tmp_path):
    # Review of bca5acd: the +6 h laptop reading applies to harness 1.0.0 rows
    # only; rows from >= 1.1.0 are already UTC.
    mod = _mod(monkeypatch, tmp_path)
    assert mod.offset_hours("1.0.0") == 6
    assert mod.offset_hours("1.1.0") == 0 and mod.offset_hours("1.10.0") == 0
