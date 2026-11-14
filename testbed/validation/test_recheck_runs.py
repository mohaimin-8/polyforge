"""recheck_runs.py leaves a run alone unless its only doubt was the load spread."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import recheck_runs  # noqa: E402


def test_a_run_without_the_spread_problem_is_not_touched(tmp_path):
    run = tmp_path / "cell01" / "run0"
    run.mkdir(parents=True)
    rec = {"cell": 1, "valid": False, "problems": ["schedule lag p99 4000 ms"]}
    (run / "run.json").write_text(json.dumps(rec))
    assert recheck_runs.recheck(run, "http://unused") is None
    assert json.loads((run / "run.json").read_text()) == rec
