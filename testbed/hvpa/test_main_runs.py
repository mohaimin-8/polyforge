"""WP17 main runs: sized from the pilot's records, seeded from the registered draw, run only when committed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "research" / "registration"))

import main_runs  # noqa: E402
import pilot  # noqa: E402
import seeds  # noqa: E402
from test_seeds import ROUND_1000  # noqa: E402

PREDICTIONS = {"regimes": [{"width": 2, "sigma": 0.2, "gain_vs_A3": 0.134, "gain_vs_A1": 0.36}],
               "live_regimes": [{"width": 2, "sigma": 0.2}],
               "tuned_targets": {"0.2": {"cpu_default": 0.7}}}
DIGEST = "67b31188d06dbdd0170a9f9b49ce7da925db6510216323bebc2321f4d8cd429d"


def draw(**change) -> dict:
    d = {"round": 1000, "randomness": ROUND_1000["randomness"], "signature": ROUND_1000["signature"],
         "protocol_sha256": DIGEST}
    return {**d, "master_seed": seeds.master_seed(DIGEST, ROUND_1000["randomness"]), **change}


def pilot_records(root: Path, schedules: int = pilot.PAIRS) -> None:
    """Joint cheapest on every schedule; A3 and A1 dearer by a spread amount."""
    for k in range(schedules):
        for arm, cost in (("A4", 10.0), ("A3", 11.0 + 0.3 * (k % 3)), ("A1", 14.0 + 1.5 * (k % 2))):
            path = pilot.record_path(root, 2, 0.2, k, arm)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"k6": {"requests": 1000, "attainment": 1.0}, "watch": {"cost": cost}}),
                            encoding="utf-8")


def test_a_regime_runs_its_largest_contrast_rounded_up_to_whole_rounds_of_orders():
    assert main_runs.regime_size({"A3": 7, "A1": 3}) == 12
    assert main_runs.regime_size({"A3": 2, "A1": 1}) == 6
    with pytest.raises(SystemExit, match="cannot size"):
        main_runs.regime_size({"A3": None, "A1": 4})


def test_the_plan_is_sized_by_the_pilot_and_seeded_by_the_draw(tmp_path):
    pilot_records(tmp_path)
    plan = main_runs.make_plan(tmp_path, PREDICTIONS, draw(), {})
    (reg,) = plan["regimes"]
    n = reg["schedules_n"]
    assert n % 6 == 0 and n >= max(c["n_superiority"] for c in reg["contrasts"].values())
    master = plan["draw"]["master_seed"]
    assert [s["seed"] for s in reg["schedules"]] == [seeds.run_seed(master, f"wp17|w2|s0.2|k{k}") for k in range(n)]
    assert len({s["seed"] for s in reg["schedules"]}) == n
    assert [tuple(s["order"]) for s in reg["schedules"][:6]] == [pilot.order(k) for k in range(6)]
    assert reg["targets"] == {"A4": 2, "A3": 2, "A1": 0.7}


def test_equivalence_can_be_named_per_comparator(tmp_path):
    pilot_records(tmp_path)
    plan = main_runs.make_plan(tmp_path, PREDICTIONS, draw(), main_runs.parse_tests(["A3=equivalence"]))
    assert plan["regimes"][0]["contrasts"]["A3"]["test"] == "equivalence"
    with pytest.raises(SystemExit, match="--test takes"):
        main_runs.parse_tests(["A2=superiority"])


def test_a_draw_that_does_not_add_up_is_refused(tmp_path):
    pilot_records(tmp_path)
    with pytest.raises(SystemExit, match="master seed"):
        main_runs.make_plan(tmp_path, PREDICTIONS, draw(master_seed="00" * 32), {})
    with pytest.raises(SystemExit, match="not a valid draw"):
        main_runs.make_plan(tmp_path, PREDICTIONS, draw(randomness="00" * 32), {})


def test_an_unfinished_pilot_sizes_nothing(tmp_path):
    pilot_records(tmp_path, schedules=1)
    with pytest.raises(SystemExit, match="pilot is not done"):
        main_runs.make_plan(tmp_path, PREDICTIONS, draw(), {})


def test_no_main_run_from_an_uncommitted_plan(tmp_path, monkeypatch):
    import run_pilot

    plan = tmp_path / "main_plan.json"
    plan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_pilot, "committed_unchanged", lambda path: False)
    with pytest.raises(SystemExit, match="not committed"):
        main_runs.run_plan(plan, tmp_path / "out")


def test_a_plan_is_written_once(tmp_path):
    out = tmp_path / "main_plan.json"
    out.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="written once"):
        main_runs.main(["plan", "--pilot", str(tmp_path), "--draw", str(tmp_path / "d.json"), "--out", str(out)])
