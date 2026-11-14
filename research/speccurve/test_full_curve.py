"""full_curve.py on synthetic campaigns with a known answer, and its
agreement with WP9's own protocol (objective and selection)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import full_curve as fc  # noqa: E402
import protocol  # noqa: E402  (research/comparators, on the path through full_curve)

ARMS = ("joint", "per_knob", "one_knob", "layers", "rules")


def metrics(rng: np.random.Generator, n: int, cost_shift: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame({"total_cost_usd": rng.uniform(5.0, 20.0, n) + cost_shift, "steps": 120,
                         "mean_violation": rng.uniform(0.0, 0.3, n), "mean_jain": rng.uniform(0.6, 1.0, n),
                         "mean_quality_loss": rng.uniform(0.0, 0.4, n),
                         "ai_shed_request_share": rng.uniform(0.0, 0.1, n)})


def rows(rng, phase: str, workloads: list[str], configs: int, joint_better: float = 0.0) -> pd.DataFrame:
    frames = []
    for arm in ARMS:
        for c in range(configs):
            m = metrics(rng, len(workloads), 0.0 if arm == "joint" else joint_better)
            frames.append(m.assign(phase=phase, arm=arm, config=c, workload=workloads))
    return pd.concat(frames, ignore_index=True)


def classes_of(names: list[str]) -> dict[str, str]:
    return {w: ("api_or_code" if i % 2 else "conversation") for i, w in enumerate(names)}


def campaign(rng, plant="B", budget="enforced", configs=4, n_t=8, n_e=6, joint_better=0.0, full_eval=True):
    tw, ew = [f"t{i:02d}" for i in range(n_t)], [f"e{i:02d}" for i in range(n_e)]
    tc, ec = classes_of(tw), classes_of(ew)
    tuned = {"classes": {half: {k: [w for w in cl if cl[w] == k] for k in sorted(set(cl.values()))}
                         for half, cl in (("tuning", tc), ("evaluation", ec))},
             "settings": {"plant": plant, "mix": "own", "steps": 120, "cluster_size": "large",
                          "budget": budget, "quality_weight": 0.0},
             "arms": {}}
    tuning = rows(rng, "tuning", tw, configs, joint_better)
    evaluation = rows(rng, "evaluation", ew, configs, joint_better) if full_eval else rows(rng, "evaluation", ew, 1)
    c = fc.Campaign(plant, budget, Path(f"plant{plant}_own_large_s120_{budget}_00000000"), tuned, tuning,
                    evaluation[["arm", "config", "workload", *fc.METRICS]])
    sel = fc.select(tuning, tc, fc.WP9_OBJECTIVE)
    for arm, s in sel.items():
        c.tuned["arms"][arm] = {"configs": [{} for _ in range(configs)], "overall": {"config": s["overall"]},
                                "per_class": {k: {"config": v} for k, v in s["per_class"].items()}}
    return c


def test_the_score_at_wp9_weights_is_the_protocols_J_term_for_term():
    df = metrics(np.random.default_rng(3), 300)
    ours = fc.score(df, fc.WP9_OBJECTIVE)
    theirs = [protocol.objective({**r, "steps": int(r["steps"])}, fc.TENANTS)["J"] for r in df.to_dict("records")]
    assert np.array_equal(ours, np.array(theirs))


@pytest.mark.parametrize("w", [fc.WP9_OBJECTIVE, (4.0, 0.0, 1.0, 1.0), (1.0, 0.5, 0.5, 0.0)])
def test_selection_is_protocol_select_with_failures_and_ties(w):
    rng = np.random.default_rng(11)
    tw = [f"t{i:02d}" for i in range(10)]
    tuning = rows(rng, "tuning", tw, 6)
    rules = tuning.arm == "rules"
    tuning.loc[rules & (tuning.config == 4), fc.METRICS] = tuning.loc[rules & (tuning.config == 1), fc.METRICS].values
    tuning.loc[(tuning.arm == "joint") & (tuning.config == 2) & (tuning.workload == "t03"), "mean_violation"] = np.nan
    classes = classes_of(tw)
    ours = fc.select(tuning, classes, w)
    picks = protocol.select([{"phase": "tuning", "arm": r.arm, "config": int(r.config), "workload": r.workload,
                              "J": s} for r, s in zip(tuning.itertuples(), fc.score(tuning, w))], classes)
    assert ours == {arm: {"overall": p["overall"].config, "per_class": {k: v.config for k, v in p["per_class"].items()}}
                    for arm, p in picks.items()}
    assert 2 not in {ours["joint"]["overall"], *ours["joint"]["per_class"].values()}   # failed once: out


def test_the_check_against_wp9_catches_a_selection_that_differs():
    c = campaign(np.random.default_rng(5))
    fc.check_against_wp9(c)                                          # built from the same rule: passes
    c.tuned["arms"]["rules"]["overall"]["config"] = (c.tuned["arms"]["rules"]["overall"]["config"] + 1) % 4
    with pytest.raises(SystemExit):
        fc.check_against_wp9(c)


def test_needed_lists_only_the_evaluation_runs_missing():
    c = campaign(np.random.default_rng(7), full_eval=False)         # WP9 evaluated config 0 only
    todo = fc.needed(c)
    have = set(zip(c.evaluation.arm, c.evaluation.config, c.evaluation.workload))
    assert todo and not any((p["arm"], p["config"], p["workload"]) in have for p in todo)
    assert len({(p["arm"], p["config"], p["workload"]) for p in todo}) == len(todo)
    full = campaign(np.random.default_rng(7))
    assert fc.needed(full) == []


def test_effects_are_positive_when_the_joint_planner_is_better_everywhere():
    rng = np.random.default_rng(9)
    camps = {(p, b): campaign(rng, p, b, joint_better=40.0) for p in ("A", "B") for b in ("enforced", "plant")}
    specs, d, workloads = fc.effects(camps)
    assert len(specs) == np.prod([len(v) for v in fc.CHOICES.values()]) and d.shape == (len(specs), 6)
    assert (d > 0).all()


def test_find_takes_the_one_finished_campaign_and_not_a_quality_variant(tmp_path):
    for name, done in (("plantB_own_large_s120_enforced_abcd1234", True),
                       ("plantB_own_large_s120_enforced_q0.5_abcd1234", True),
                       ("plantB_own_large_s120_plant_12345678", False)):
        (tmp_path / name).mkdir()
        if done:
            (tmp_path / name / "tuned.json").write_text("{}")
    assert fc.find("B", "enforced", tmp_path).name == "plantB_own_large_s120_enforced_abcd1234"
    with pytest.raises(SystemExit):
        fc.find("B", "plant", tmp_path)                              # not finished: no tuned.json


def test_a_plants_two_budget_campaigns_must_share_the_plant_constants():
    rng = np.random.default_rng(13)
    camps = {(p, b): campaign(rng, p, b) for p in ("A", "B") for b in ("enforced", "plant")}
    for c in camps.values():
        c.tuned["settings"]["plant_digest"] = "abc" if c.plant == "A" else "def"
    fc.same_plant(camps)
    camps[("B", "plant")].tuned["settings"]["plant_digest"] = "xyz"
    with pytest.raises(SystemExit):
        fc.same_plant(camps)
    del camps[("B", "plant")].tuned["settings"]["plant_digest"]
    with pytest.raises(SystemExit):
        fc.same_plant(camps)
