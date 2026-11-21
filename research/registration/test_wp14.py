"""WP14: the confirmatory campaign's job list and its frozen scorer (no simulation runs here)."""

from __future__ import annotations

import itertools
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.weightstats import ttost_paired

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


def test_a_failed_run_fails_a_rehearsal_and_is_missing_data_in_the_campaign(tmp_path, monkeypatch, capsys, design):
    job = {"identity": "dose|w1q0|agentic|uniform|joint", "seed": 7}
    monkeypatch.setattr(campaign, "plan", lambda master, d, rehearsal=False: ([job], []))
    monkeypatch.setattr(campaign, "write_population", lambda path, workloads: None)
    monkeypatch.setattr(campaign, "execute", lambda jobs, pop, workers: [{**job, "J": math.nan, "error": "boom"}])
    assert campaign.main(["--rehearsal", "--out", str(tmp_path / "r")]) == 1
    assert "rehearsal failed: dose|w1q0|agentic|uniform|joint: boom" in capsys.readouterr().err
    final = tmp_path / "design.json"
    final.write_text(json.dumps({**design, "draft": False}), encoding="utf-8")
    assert campaign.main(["--master", MASTER, "--design", str(final), "--out", str(tmp_path / "c")]) == 0
    assert pd.read_csv(tmp_path / "c" / "rows.csv").error.tolist() == ["boom"]
    monkeypatch.setattr(campaign, "execute", lambda jobs, pop, workers: [{**job, "J": 1.0, "error": ""}])
    assert campaign.main(["--rehearsal", "--out", str(tmp_path / "ok")]) == 0


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


def test_the_statistics_agree_with_scipy_and_statsmodels():
    """The frozen statistics against independent implementations: TOST against statsmodels'
    ttost_paired, the one-sided t-test and both intervals against scipy, Holm against statsmodels'
    multipletests (a p that could not be computed sorts last, as p = 1 does), Spearman (with ties)
    and the least-squares line against scipy."""
    rng = np.random.default_rng(20261121)
    for case in range(200):
        n = int(rng.integers(3, 60))
        joint = rng.normal(0.2, 0.05, n)
        arm = joint + rng.normal(rng.normal(0, 0.005), rng.uniform(0.0005, 0.02), n)
        d, margin = arm - joint, float(rng.uniform(0.0005, 0.02))
        t, s = score.tost(d, margin), score.superiority(d)
        assert t["p"] == pytest.approx(ttost_paired(arm, joint, -margin, margin)[0], abs=1e-12)
        assert s["p"] == pytest.approx(stats.ttest_1samp(d, 0.0, alternative="greater").pvalue, abs=1e-12)
        two_sided = stats.ttest_1samp(d, 0.0)
        for level, key in ((0.90, "ci90"), (0.95, "ci95")):
            assert t[key] == pytest.approx(list(two_sided.confidence_interval(level)), abs=1e-12)
        assert (t["p"] < 0.05) == (-margin < t["ci90"][0] and t["ci90"][1] < margin)
        x = rng.integers(0, 4, n).astype(float) if case % 3 == 0 else rng.normal(size=n)   # ties
        y = rng.normal(size=n) + x
        if np.ptp(x) > 0:
            assert score.spearman(x, y) == pytest.approx(stats.spearmanr(x, y).statistic, abs=1e-12)
            fit = stats.linregress(x, y)
            assert score.line(x, y) == pytest.approx((fit.intercept, fit.slope), abs=1e-9)
    family = ["H2", "H3", "H4"]
    for _ in range(500):
        p = rng.uniform(0, 0.2, 3)
        p[rng.random(3) < 0.15] = np.nan
        adjusted = score.holm(dict(zip(family, p)), family)
        reference = multipletests(np.where(np.isnan(p), 1.0, p), method="holm")[1]
        for h, ph, ref in zip(family, p, reference):
            assert math.isnan(adjusted[h]) if math.isnan(ph) else adjusted[h] == pytest.approx(ref, abs=1e-12)


def test_the_permutation_p_estimates_the_exact_one():
    """H2's Monte Carlo p against the exact one over all 720 orderings of six regimes' gains."""
    rng = np.random.default_rng(5)
    for strength, ties in ((0.0, False), (0.8, False), (2.0, False), (0.8, True)):
        x = rng.normal(size=6)
        y = rng.normal(size=6) + strength * x
        y = np.round(y) if ties else y
        rx, ry = stats.rankdata(x), stats.rankdata(y)
        observed = np.corrcoef(rx, ry)[0, 1]
        exact = np.mean([np.corrcoef(rx, p)[0, 1] >= observed - 1e-12 for p in itertools.permutations(ry)])
        p = score.permutation_p(x, y, 10_000, 11)
        assert abs(p - exact) < 4.5 * math.sqrt(max(exact * (1 - exact), 1e-4) / 10_000) + 1 / 10_001


def test_h2s_calibration_regimes_are_the_pilot_reports(design):
    """The scorer's per-regime means of the WP4 pilot, from which H2's calibration line comes, are
    the pilot report's: 25 regimes of 20 design points and the rank correlations it gives."""
    text = (campaign.REPO / "research/theory/DOSE_RESPONSE_PILOT.md").read_text(encoding="utf-8")
    quoted = dict(re.findall(r"^\| ([a-z -]+) \| ([+-]\d\.\d\d) \|", text, re.M))
    runs = pd.read_csv(campaign.REPO / design["tests"]["H2"]["calibration"], float_precision="round_trip")
    for predictor, label in (("index", "worst-case index"), ("quantile_index", "quantile index"),
                             ("loss", "realised one-step loss"), ("costly", "share of costly disagreements")):
        reg = score.per_regime(runs, predictor)
        assert len(reg) == 25 and set(reg.n) == {20}
        assert f"{score.spearman(reg.x, reg.gain):+.2f}" == quoted[label], predictor


def test_h1_holds_only_when_some_decision_was_checked():
    every_run_failed = pd.DataFrame({"error": ["ValueError: a", "ValueError: b"]})
    h1 = score.score_h1(every_run_failed)
    assert not h1["holds"] and h1["checked"] == 0 and h1["failed_runs"] == 2
    nothing_applied = pd.DataFrame({"error": ["", ""], "theorem_applies": [0, 0], "theorem_violations": [0, 0]})
    assert not score.score_h1(nothing_applied)["holds"]
    some = pd.DataFrame({"error": ["", "boom"], "decisions": [8, np.nan], "theorem_applies": [5, np.nan],
                         "theorem_violations": [0, np.nan]})
    h1 = score.score_h1(some)
    assert h1["holds"] and h1["checked"] == 5 and h1["violations"] == 0 and h1["failed_runs"] == 1
    assert h1["logged"] == 8                       # the completed runs' decisions, checked or not
    assert not score.score_h1(some.assign(theorem_violations=[1, np.nan]))["holds"]


def test_the_h1_row_says_the_theorem_was_checked_where_it_applies_of_all_logged(planned, design):
    """The WP4 pilot's line gave only the checked count 'over all logged decisions', and the
    protocol draft read it as 'held on all 456,876 logged decisions' (480,000 were logged)."""
    jobs, _ = planned
    pilot = score.per_regime(pd.read_csv(campaign.REPO / design["tests"]["H2"]["calibration"]), "index")
    s = score.score(synthetic(jobs, score.line(pilot.x, pilot.gain), plant_a_shift=0.0), design, MASTER)
    text = score.report(s, {"master": MASTER, "rehearsal": False, "alpha": 0.05, "holm": ["H2", "H3", "H4"],
                            "design_sha256": "0" * 64})
    h1 = s["hypotheses"]["H1"]
    assert 0 < h1["checked"] < h1["logged"]
    row = next(r for r in text.splitlines() if r.startswith("| H1 |"))
    assert "where it applies" in row and f"{h1['checked']:,} of {h1['logged']:,} logged decisions" in row


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


def nudged(value):
    """`value` with its first non-zero float moved by one unit in the last place."""
    moved = False

    def walk(v):
        nonlocal moved
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, float) and v != 0.0 and not moved:
            moved = True
            return float(np.nextafter(v, math.inf))
        return v

    out = walk(json.loads(json.dumps(value)))
    assert moved
    return out


def test_the_scorer_says_whether_every_shard_ran_the_drawn_workloads(planned, tmp_path):
    _, workloads = planned
    for i, cpu in enumerate(("AMD EPYC 7763", "Intel Xeon 8370C")):
        (tmp_path / f"shard-{i}").mkdir()
        campaign.write_population(tmp_path / f"shard-{i}" / "population.json", workloads)
        (tmp_path / f"shard-{i}" / "provenance.json").write_text(json.dumps({"cpu": cpu}), encoding="utf-8")
    same = score.population_check(tmp_path, workloads)
    assert (same["shards"], same["versions"], same["differ"], same["largest_difference"]) == (2, 1, [], 0.0)
    assert "every shard ran the same 3 workloads" in score.population_line(same)
    campaign.write_population(tmp_path / "shard-1" / "population.json", nudged(workloads))
    one_ulp = score.population_check(tmp_path, workloads)
    assert (one_ulp["versions"], one_ulp["differ"]) == (2, [1])
    assert 0 < one_ulp["largest_difference"] < 1e-15
    assert one_ulp["by_shard"][1]["cpu"] == "Intel Xeon 8370C"
    assert "1 of 2 shards differ" in score.population_line(one_ulp)
    renamed = json.loads(json.dumps(workloads))
    renamed[0]["workload"] = "pop999"
    assert score.largest_difference(workloads, renamed) == math.inf


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
            r.update(J=1.0 + (a + b * x if j["search"] == "decoupled" else 0.0), decisions=12, theorem_applies=10,
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


def test_the_report_gives_h3s_90_and_95_percent_intervals(planned, design):
    """PROTOCOL.md section 6 reports H3's 90% and 95% intervals of the mean of d. The TOST at 0.05
    holds exactly when the 90% interval lies inside the margins, so SCORE.md shows it beside the
    95% one (a 95% interval alone can cross a margin while H3 is supported)."""
    jobs, _ = planned
    pilot = score.per_regime(pd.read_csv(campaign.REPO / design["tests"]["H2"]["calibration"]), "index")
    s = score.score(synthetic(jobs, score.line(pilot.x, pilot.gain), plant_a_shift=0.0), design, MASTER)
    text = score.report(s, {"master": MASTER, "rehearsal": False, "alpha": 0.05, "holm": ["H2", "H3", "H4"],
                            "design_sha256": "0" * 64})
    row = {k: next(r for r in text.splitlines() if r.startswith(f"| {k} |")) for k in ("H3", "H4")}
    h3, h4 = s["hypotheses"]["H3"], s["hypotheses"]["H4"]
    for level in ("90", "95"):
        lo, hi = h3[f"ci{level}"]
        assert f"{level}% CI [{score.f(lo)}, {score.f(hi)}]" in row["H3"]
    assert f"95% CI [{score.f(h4['ci95'][0])}, {score.f(h4['ci95'][1])}]" in row["H4"]
    assert "90% CI" not in row["H4"]


def test_the_comparison_runs_only_on_the_plant_wp9_tuned_on(monkeypatch):
    """PROTOCOL.md section 4 names the plant digests WP9's tuned.json files record. A plant 2 changed
    since (plant.py, CONSTANTS, fits) would run WP9's selected configurations on another plant."""
    import tune

    design = json.loads(campaign.DESIGN.read_text(encoding="utf-8"))
    comp = design["comp"]
    for plant, rel in comp["plants"].items():
        settings = json.loads((campaign.REPO / rel).read_text(encoding="utf-8"))["settings"]
        assert settings["plant_digest"] == tune.plant_digest(plant)
        campaign.check_settings(plant, settings, comp, rel)
        monkeypatch.setattr(tune, "plant_digest", lambda mode: "0" * 12)
        with pytest.raises(SystemExit, match="another plant"):
            campaign.check_settings(plant, settings, comp, rel)
        monkeypatch.undo()
