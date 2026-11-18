"""WP14: the confirmatory campaign's job list and its frozen scorer (no simulation runs here)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wp14_campaign as campaign  # noqa: E402
import wp14_score as score  # noqa: E402

MASTER = "ab" * 32
OTHER = "cd" * 32
PILOT_KAPPAS = (0.0, 0.25, 0.5, 1.0, 2.0)
ARMS = ("joint", "per_knob", "one_knob", "layers", "rules")


@pytest.fixture(scope="module")
def design() -> dict:
    """The draft design with three drawn workloads (each costs eight model fits)."""
    d = json.loads(campaign.DESIGN.read_text(encoding="utf-8"))
    return {**d, "comp": {**d["comp"], "workloads": 3}}


@pytest.fixture(scope="module")
def planned(design):
    return campaign.plan(MASTER, design)


# --------------------------------------------------------------------------
# The job list
# --------------------------------------------------------------------------

def test_new_regimes_cross_every_width_with_one_drawn_kappa_per_stratum(design):
    dose = design["dose"]
    regs = campaign.regimes(MASTER, dose)
    assert len(regs) == len(dose["widths"]) * dose["kappa_strata"]
    step = (dose["kappa_max"] - dose["kappa_min"]) / dose["kappa_strata"]
    for r in regs:
        q = int(r["regime"].split("q")[1])
        assert dose["kappa_min"] + q * step <= r["kappa"] < dose["kappa_min"] + (q + 1) * step
        assert r["kappa"] not in PILOT_KAPPAS
    assert campaign.regimes(MASTER, dose) == regs
    assert [r["kappa"] for r in campaign.regimes(OTHER, dose)] != [r["kappa"] for r in regs]


def test_the_design_sets_the_sizes(planned, design):
    jobs, workloads = planned
    dose = design["dose"]
    assert sum(j["part"] == "dose" for j in jobs) == (len(dose["widths"]) * dose["kappa_strata"] * len(dose["workloads"])
                                                     * len(dose["mixes"]) * len(campaign.SEARCHES))
    assert len(workloads) == design["comp"]["workloads"]
    comp = [j for j in jobs if j["part"] == "comp"]
    for plant in design["comp"]["plants"]:
        for arm in ARMS:
            for g in design["comp"]["granularities"]:
                assert sum(1 for j in comp if j["plant"] == plant and j["arm"] == arm
                           and g in j["granularities"]) == len(workloads)


def test_every_seed_comes_from_the_master_and_the_arms_share_demand(planned):
    jobs, _ = planned
    by_point, by_workload = {}, {}
    for j in jobs:
        if j["part"] == "dose":
            by_point.setdefault((j["workload"], j["tenant_mix"]), set()).add(j["seed"])
        else:
            by_workload.setdefault(j["workload"], set()).add(j["seed"])
    assert all(len(s) == 1 for s in by_point.values())       # every regime and arm of a design point
    assert all(len(s) == 1 for s in by_workload.values())    # every plant, arm and configuration of a workload
    assert len({s for (s,) in by_point.values()}) == len(by_point)
    assert len({s for (s,) in by_workload.values()}) == len(by_workload)
    assert all(0 <= j["seed"] < campaign.SEED_RANGE for j in jobs)
    w = jobs[-1]["workload"]
    assert jobs[-1]["seed"] == campaign.seeds.run_seed(MASTER, f"comp|demand|{w}") % campaign.SEED_RANGE


def test_drawn_workloads_are_new_names_and_follow_the_draw(planned):
    _, workloads = planned
    names = [w["workload"] for w in workloads]
    assert names[0] == "pop1000" and len(set(names)) == len(names)
    registered = json.loads((campaign.REPO / "research" / "population" / "data" / "population.json")
                            .read_text(encoding="utf-8"))["workloads"]
    assert not set(names) & {w["workload"] for w in registered}
    assert campaign.draw_population(MASTER, 2) == workloads[:2]      # the first n of a longer draw
    assert campaign.draw_population(OTHER, 2) != workloads[:2]


def test_comp_jobs_run_the_configurations_wp9_selected(planned, design):
    jobs, _ = planned
    for plant, rel in design["comp"]["plants"].items():
        tuned = json.loads((campaign.REPO / rel).read_text(encoding="utf-8"))
        for j in (j for j in jobs if j["part"] == "comp" and j["plant"] == plant):
            arm = tuned["arms"][j["arm"]]
            assert j["params"] == arm["configs"][j["config"]]
            if "overall" in j["granularities"]:
                assert j["config"] == arm["overall"]["config"]


def test_a_tuned_file_for_another_setting_is_refused(design):
    with pytest.raises(SystemExit, match="was tuned for"):
        campaign.comp_jobs(MASTER, {**design["comp"], "budget": "plant"}, campaign.draw_population(MASTER, 2))


def test_a_draft_design_runs_only_as_a_rehearsal(tmp_path, design):
    p = tmp_path / "design.json"
    p.write_text(json.dumps({**design, "draft": True}), encoding="utf-8")
    with pytest.raises(SystemExit, match="draft"):
        campaign.load_design(p)
    assert campaign.load_design(p, rehearsal=True)["draft"] is True
    p.write_text(json.dumps({**design, "draft": False}), encoding="utf-8")
    assert campaign.load_design(p)["draft"] is False


def test_check_tells_whether_commit_c_is_ready(tmp_path, design, capsys):
    p = tmp_path / "design.json"
    p.write_text(json.dumps({**design, "draft": True}), encoding="utf-8")
    with pytest.raises(SystemExit, match="draft"):
        campaign.main(["--check", "--design", str(p)])
    p.write_text(json.dumps({**design, "draft": False}), encoding="utf-8")
    assert campaign.main(["--check", "--design", str(p)]) == 0
    assert "is final" in capsys.readouterr().out


def test_the_master_is_the_draws_and_a_rehearsal_never_takes_one(tmp_path):
    with pytest.raises(SystemExit, match="64 lower-case hex"):
        campaign.main(["--master", "AB" * 32, "--out", str(tmp_path)])
    with pytest.raises(SystemExit, match="made-up"):
        campaign.main(["--rehearsal", "--master", MASTER, "--out", str(tmp_path)])


# --------------------------------------------------------------------------
# The scorer
# --------------------------------------------------------------------------

def test_tost_and_superiority_on_known_differences():
    d = np.array([0.001, -0.001, 0.0005, -0.0005] * 10)
    eq = score.tost(d, 0.01)
    assert eq["p"] < 1e-6 and eq["n"] == 40
    assert score.tost(d + 0.02, 0.01)["p"] > 0.5
    sup = score.superiority(d + 0.01)
    assert sup["p"] < 1e-6 and sup["ci95"][0] > 0
    assert score.superiority(d - 0.01)["p"] > 0.99
    assert math.isnan(score.superiority(np.array([0.1]))["p"])


def test_holm_counts_the_whole_family():
    family = ["H2", "H3", "H4"]
    assert score.holm({"H2": 0.01, "H3": 0.04, "H4": 0.03}, family) == pytest.approx(
        {"H2": 0.03, "H3": 0.06, "H4": 0.06})
    adj = score.holm({"H2": math.nan, "H3": 0.02, "H4": 0.01}, family)
    assert adj["H4"] == pytest.approx(0.03) and adj["H3"] == pytest.approx(0.04) and math.isnan(adj["H2"])


def test_rank_correlation_and_its_permutation_p():
    x = np.arange(20.0)
    assert score.spearman(x, x ** 2) == pytest.approx(1.0)
    assert score.permutation_p(x, x ** 2, 2000, 1) == pytest.approx(1 / 2001)
    assert score.permutation_p(x, -x, 2000, 1) == pytest.approx(1.0)


def test_the_shards_must_hold_every_job_once_with_its_seed(planned):
    jobs, _ = planned
    rows = pd.DataFrame([{"identity": j["identity"], "seed": j["seed"]} for j in jobs])
    score.check_coverage(rows, jobs)
    with pytest.raises(SystemExit, match="1 duplicated"):
        score.check_coverage(pd.concat([rows, rows.iloc[:1]], ignore_index=True), jobs)
    with pytest.raises(SystemExit, match="1 missing"):
        score.check_coverage(rows.iloc[1:], jobs)
    bad = rows.copy()
    bad.loc[0, "seed"] += 1
    with pytest.raises(SystemExit, match="another seed"):
        score.check_coverage(bad, jobs)


def synthetic(jobs: list[dict], line: tuple[float, float], plant_a_shift: float) -> pd.DataFrame:
    """Rows with known answers: each regime's gain on the pilot's line; per-knob ties joint on plant A
    (shifted by `plant_a_shift`) and costs 0.01 more on plant B."""
    rng = np.random.default_rng(0)
    a, b = line
    rows = []
    for j in jobs:
        r = {k: v for k, v in j.items() if k != "params"}
        r["error"] = ""
        if j["part"] == "dose":
            x = 0.1 + j["width"] * (0.05 + j["kappa"]) / 8
            r.update(J=1.0 + (a + b * x if j["search"] == "decoupled" else 0.0), theorem_applies=10,
                     theorem_violations=0, index=x if j["search"] == "joint" else math.nan)
        else:
            base = 0.2 + 0.0001 * (int(j["workload"][3:]) % 7)
            shift = {"joint": 0.0, "per_knob": plant_a_shift if j["plant"] == "A" else 0.01}.get(j["arm"], 0.005)
            r.update(J=base + shift + (0.0 if j["arm"] == "joint" else rng.normal(0, 0.0005)),
                     total_cost_usd=10.0 + shift, mean_violation=0.01, granularities="+".join(j["granularities"]))
        rows.append(r)
    return pd.DataFrame(rows)


def test_the_scorer_finds_known_answers(planned, design):
    jobs, _ = planned
    pilot = score.per_regime(pd.read_csv(campaign.REPO / design["tests"]["H2"]["calibration"]), "index")
    line = score.line(pilot.x, pilot.gain)
    s = score.score(synthetic(jobs, line, plant_a_shift=0.0), design, MASTER)
    h = s["hypotheses"]
    assert h["H1"]["holds"] and h["H1"]["violations"] == 0
    assert h["H2"]["rho"] == pytest.approx(1.0) and h["H2"]["calibration_slope"] == pytest.approx(1.0)
    assert all(h[k]["supported"] for k in ("H2", "H3", "H4"))
    assert h["H4"]["mean_d"] == pytest.approx(0.01, abs=1.5e-3)    # three pairs, noise sd 0.0005
    text = score.report(s, {"master": MASTER, "rehearsal": False, "alpha": 0.05, "holm": ["H2", "H3", "H4"],
                            "design_sha256": "0" * 64})
    assert "| H3 |" in text and "| H4 |" in text
    json.dumps(score.clean(s), allow_nan=False)
    h = score.score(synthetic(jobs, line, plant_a_shift=0.01), design, MASTER)["hypotheses"]
    assert not h["H3"]["supported"]
