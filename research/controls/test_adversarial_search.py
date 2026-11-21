"""WP22 adversarial search: the space stays valid, the objective is the one
documented, pooled workers carry no state between jobs, and a smoke search
runs end to end."""

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adversarial_search as adv  # noqa: E402
import controls_common as cc  # noqa: E402


def _reset_plant():
    from harness import sim_backend

    sim_backend._apply_economy(())
    sim_backend._apply_model_form(())


def test_sampled_and_neighbouring_regimes_are_valid_plants():
    from harness import sim_backend
    from harness.config import ECONOMY_KEYS, MODEL_FORM_KEYS

    rng = random.Random(3)
    thetas = [adv.sample(rng, max_width=8) for _ in range(150)]
    thetas += [adv.neighbour(t, rng, max_width=8) for t in thetas]
    try:
        for t in thetas:
            for p in adv.SPACE:
                v = t[p.name]
                if p.kind in ("float", "log"):
                    assert p.low <= v <= p.high
                elif p.kind == "choice":
                    assert v in p.choices
            assert t["width"] <= 8
            form, economy = adv.plant_of(t)
            assert set(form) <= MODEL_FORM_KEYS and set(economy) <= ECONOMY_KEYS
            sim_backend._apply_model_form(tuple(sorted(form.items())))  # raises on an invalid form
            sim_backend._apply_economy(tuple(sorted(economy.items())))
    finally:
        _reset_plant()


def test_objective_key_is_the_documented_one():
    def key(G, I, mode="constrained", two_sided=False):
        return adv.objective_key({"G": G, "I": I}, mode, 0.25, 0.05, two_sided)

    assert key(0.01, 0.2) > key(10.0, 0.3)  # under tau beats any gain over it
    assert key(0.02, 0.2) > key(0.01, 0.1)  # under tau: larger gain wins
    assert key(0.0, 0.3) > key(0.0, 0.9)  # over tau: closer to tau wins
    assert key(-1.0, 0.0) > key(5.0, math.nan)  # an undefined index ranks last
    assert key(-0.05, 0.1, two_sided=True) > key(0.04, 0.1, two_sided=True)
    ratio = key(0.03, 0.55, "ratio")  # G / (I + eps)
    assert ratio[0] == 1 and math.isclose(ratio[1], 0.03 / 0.6, rel_tol=1e-12)


def test_pooled_worker_state_does_not_leak_between_jobs():
    """B, then a very different A, then B again in one process: B's two rows
    are identical, and no arm is left in the registry."""
    from harness.systems import SYSTEMS

    rng = random.Random(11)
    b = {**adv.sample(rng, max_width=1), "workload": "joint_stress", "cluster_size": "small"}
    a = {**b, "kappa": 3.0, "latency": "mode_b", "self_hosted": True, "admission_gate": True,
         "attainment_slo": True, "mixture_p95": True, "cache_hit_max": 0.9, "replica_capacity_wu": 30.0,
         "workload": "agentic"}
    dp = adv.panel_of(b, 1)[0]
    try:
        first = adv.eval_point((0, b, dp, 6, 0))
        other = adv.eval_point((1, a, adv.panel_of(a, 1)[0], 6, 0))
        again = adv.eval_point((0, b, dp, 6, 0))
    finally:
        _reset_plant()
    def same(x, y):  # NaN-aware: a NaN summary field equals a NaN
        return x == y or (isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y))

    assert first.keys() == again.keys()
    assert all(same(first[k], again[k]) for k in first if k != "wall_s")
    assert other["J_joint"] != first["J_joint"]  # A really is a different world: the check has teeth
    assert cc.JOINT not in SYSTEMS and cc.DECOUPLED not in SYSTEMS


def test_population_panels_come_from_the_tuning_split_only():
    from harness import population_workloads

    tuning = set(population_workloads.names("tuning"))
    panel = adv.panel_of({"workload": "population", "cluster_size": "large"}, 10)
    assert len(panel) == 10 and all(w in tuning and m == "own" for w, m, _ in panel)


def test_smoke_search_runs_end_to_end():
    args = adv.parse_args(["--smoke"])
    try:
        ev = adv.search(args, None)
        confirmed = adv.confirm(ev, args)
    finally:
        _reset_plant()
    assert len([r for r in ev.regimes if not r["phase"].startswith("confirm")]) == args.budget
    assert len(confirmed) == 1 and confirmed[0]["points"] == args.panel * args.confirm_reps
    assert all(r["theorem_violations"] == 0 for r in ev.regimes)
    text = adv.report(ev, confirmed, args, "test")
    assert "violations **0**" in text and "Confirmation on fresh seeds" in text


@pytest.mark.parametrize("tag, name", [("search", "ADVERSARIAL_SEARCH.md"), ("smoke", "ADVERSARIAL_SMOKE.md")])
def test_the_report_is_rebuilt_from_the_committed_record(tag, name):
    ev, confirmed, args, label = adv.from_record(tag)
    assert adv.report(ev, confirmed, args, label) == (cc.HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_confirmations_that_repeat_another_regimes_runs_are_counted_once():
    """Regimes 163 and 164 differ from 158 only in parameters that changed no fresh run: their
    confirmations are 158's runs again, so the table holds one confirmation, not three."""
    ev, confirmed, args, label = adv.from_record("search")
    text = adv.report(ev, confirmed, args, label)
    rows = {int(line.split("|")[1]): line for line in text.split("## Confirmation on fresh seeds")[1]
            .split("## Top regimes")[0].splitlines() if line.startswith("| 1")}
    assert rows[158].rstrip().endswith("| - |")
    assert rows[163].rstrip().endswith("| 158 |") and rows[164].rstrip().endswith("| 158 |")
    assert "1 distinct confirmation of 3" in text
    assert ("**51 regime(s) in the refutation corner during the search** (before confirmation): 35 with runs "
            "of their own, 16 repeating another's exactly; all from one phase, climb from 62.") in text
