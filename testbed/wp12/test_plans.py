"""plans.py: the design's schedules, the tuning rule and its journal."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import plans  # noqa: E402
import tb_planner as tbp  # noqa: E402

import cell_schedules as cs  # noqa: E402

DAY_BINS = 8640


def curves(tmp_path: Path, mu: float) -> Path:
    d = tmp_path / "curves" / "small_r3"
    d.mkdir(parents=True)
    (d / "load_curve_meta.json").write_text(json.dumps({"mu_trace_rps": mu}))
    return tmp_path / "curves"


def test_tuning_demand_is_what_build_cell_would_draw(tmp_path):
    c = curves(tmp_path, 0.9)
    days = np.random.default_rng(4).poisson(2.0, size=(4 * DAY_BINS,)).astype(np.int64)
    population = {"workloads": [{"split": "evaluation", "tenants": [{"stream": "s/x", "window": 30}]}]}
    prompts = [f"prompt number {i}" for i in range(5000)]
    for cell_id in (12050, 12151):
        cell = {"cell": cell_id, "target_util": 0.5, "small_share": 0.7, "stream": "s/x", "pools": [3, 1],
                "cache": "off"}
        built = cs.build_cell(cell, c, population, {"s/x": days}, DAY_BINS, lambda: iter(prompts), None)
        want = {(k, t): n for k, t, n in built["demand"] if n}
        assert plans.demand_counts(cell, c, population, {"s/x": days}, DAY_BINS) == want


def test_the_design_has_disjoint_ids_and_the_declared_counts():
    cells = plans.cells()
    ids = [c["cell"] for c in cells]
    assert len(set(ids)) == len(ids) and min(ids) >= plans.CELL_BASE > 19   # no WP11 cell id
    for regime in plans.REGIMES:
        roles = [c["role"] for c in cells if c["regime"] == regime]
        assert roles.count("live") == plans.PAIRS and roles.count("tuning") == plans.TUNING
    assert {c["stream"] for c in cells} == set(plans.STREAMS)


def test_the_regimes_are_the_lowest_and_highest_mean_index_levels(tmp_path):
    rows = [{"util": u, "mean_index": i} for u, i in ((0.3, 0.02), (0.3, 0.04), (0.6, 0.01), (0.6, 0.05),
                                                      (0.9, 0.06), (0.9, 0.06), (0.15, 0.03), (0.15, 0.03))]
    assert plans.choose_regimes(rows) == {"low": 0.15, "high": 0.9}    # 0.15 ties 0.3 and 0.6 at 0.03: lowest wins
    f = tmp_path / "regimes.json"
    assert plans.regimes_in_use(f) == plans.REGIMES
    f.write_text(json.dumps({"chosen": {"low": 0.15, "high": 0.9}}))
    assert plans.regimes_in_use(f) == {"low": 0.15, "high": 0.9}
    assert {c["target_util"] for c in plans.cells({"low": 0.15, "high": 0.9})} == {0.15, 0.9}


def test_demand_files_round_trip(tmp_path):
    d = {(0, "t00"): 3, (5, "t01"): 1, (59, "t00"): 12}
    plans.write_demand(tmp_path / "x.csv.gz", d)
    assert plans.read_demand(tmp_path / "x.csv.gz") == d
    first = (tmp_path / "x.csv.gz").read_bytes()
    plans.write_demand(tmp_path / "x.csv.gz", d)
    assert (tmp_path / "x.csv.gz").read_bytes() == first   # no timestamp in the gzip header


def rows(spec: dict) -> list[dict]:
    out = []
    for (arm, config), js in spec.items():
        for s, j in enumerate(js):
            out.append({"arm": arm, "config": config, "schedule": s, "J": j, "error": "" if j == j else "x"})
    return out


def test_selection_takes_the_lowest_mean_and_drops_failed_or_incomplete_configurations():
    nan = float("nan")
    picks = plans.select(rows({("joint", 0): [1.0, 1.0], ("joint", 1): [0.5, 0.4], ("joint", 2): [0.1, nan],
                               ("joint", 3): [0.2], ("per_knob", 0): [0.6, 0.6], ("per_knob", 1): [0.6, 0.6]}), 2)
    assert picks["joint"]["config"] == 1 and picks["joint"]["configs_scored"] == 2
    assert picks["per_knob"]["config"] == 0   # a tie goes to the lower index
    with pytest.raises(SystemExit):
        plans.select(rows({("joint", 0): [nan, 1.0], ("per_knob", 0): [1.0, 1.0]}), 2)


def test_tuning_resumes_from_its_journal(tmp_path, monkeypatch):
    calls = []

    def fake_plan_run(arm, params, demand, tb, steps, with_index=False):
        calls.append(arm)
        j = params["weight_beta"] + (0.0 if arm == "joint" else 1.0)
        return {"outcome": {"J": j, "cost_term": 0.0, "violation_term": j, "unfairness_term": 0.0,
                            "cost_per_1k": 1.0, "attainment": 0.9}}

    monkeypatch.setattr(tbp, "plan_run", fake_plan_run)
    plans._DEMAND_CACHE.clear()
    sched = []
    for k in range(2):
        rel = f"inputs/tuning/cell{12050 + k}.csv.gz"
        plans.write_demand(tmp_path / rel, {(0, "t00"): 1})
        sched.append({"cell": 12050 + k, "role": "tuning", "input": rel})
    design = tmp_path / "design.json"
    design.write_text(json.dumps({"schedules": sched}))
    out = tmp_path / "tuning"
    tuned = plans.tune(None, 1, out, "fitted", design, configs_per_arm=4)
    assert len(calls) == 2 * 4 * 2
    betas = [p["weight_beta"] for p in plans.arms.sample_configs("joint", 4, plans.WP9_SEED)]
    assert tuned["arms"]["joint"]["config"] == int(np.argmin(betas))
    assert tuned["arms"]["joint"]["params"]["weight_beta"] == min(betas)
    plans.tune(None, 1, out, "fitted", design, configs_per_arm=4)
    assert len(calls) == 2 * 4 * 2   # everything was in the journal
    assert json.loads((out / "tuned.json").read_text())["settings"]["configs"] == 4
