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


def test_sizes_reads_the_pilot_so_far_without_a_draw(tmp_path, capsys):
    pilot_records(tmp_path)
    rows = main_runs.sizes(tmp_path, PREDICTIONS)
    assert {r["arm"] for r in rows} == {"A3", "A1"} and all(r["pairs"] == pilot.PAIRS for r in rows)
    plan = main_runs.make_plan(tmp_path, PREDICTIONS, draw(), {})
    assert ({r["arm"]: r["n_superiority"] for r in rows}
            == {arm: c["n_superiority"] for arm, c in plan["regimes"][0]["contrasts"].items()})
    assert all(r["n_superiority_80"] <= r["n_superiority"] for r in rows)
    assert main_runs.hours(6) == pytest.approx(3 * 6 * 60.2 / 60)
    main_runs.print_sizes(rows)
    out = capsys.readouterr().out
    assert "every contrast" in out and "after main_score.py's Holm over the 2 contrasts" in out
    assert "every contrast at 90 % power after that Holm: w2 s0.2 " in out


def contrast(arm: str, delta: float, sigma_d: float, width: int = 2) -> dict:
    return {"width": width, "sigma": 0.2, "arm": arm, "delta": delta, "sigma_d": sigma_d}


def exact_power(delta: float, sigma_d: float, n: int, alpha: float) -> float:
    """The one-sided paired t-test's power: the noncentral t."""
    from scipy import stats
    return float(1 - stats.nct.cdf(stats.t.ppf(1 - alpha, n - 1), n - 1, delta * n ** 0.5 / sigma_d))


def test_the_simulated_power_is_the_t_tests_alone_and_holms_beside_others():
    """Known answers: one contrast alone is the exact t-test (u); beside a certain one, Holm tests it
    at alpha (u again); beside an independent twin, at alpha/2 unless the twin falls first:
    b + (u - b) b, with b the t-test's power at alpha/2."""
    draws = 20000
    tol = lambda p: 4.5 * (p * (1 - p) / draws) ** 0.5 + 1e-9  # noqa: E731
    u, b = exact_power(0.5, 1.0, 36, 0.05), exact_power(0.5, 1.0, 36, 0.025)
    alone = main_runs.holm_power([contrast("A3", 0.5, 1.0)], {(2, 0.2): 36}, draws=draws)
    assert alone[(2, 0.2, "A3")] == pytest.approx(u, abs=tol(u))
    beside_certain = main_runs.holm_power([contrast("A3", 0.5, 1.0), contrast("A1", 5.0, 1.0)],
                                          {(2, 0.2): 36}, draws=draws)
    assert beside_certain[(2, 0.2, "A1")] == 1.0
    assert beside_certain[(2, 0.2, "A3")] == pytest.approx(u, abs=tol(u))
    rows, n = [contrast("A3", 0.5, 1.0), contrast("A3", 0.5, 1.0, width=4)], {(2, 0.2): 36, (4, 0.2): 36}
    twins, holm = main_runs.holm_power(rows, n, draws=draws), b + (u - b) * b
    assert all(q == pytest.approx(holm, abs=tol(holm)) for q in twins.values())
    assert twins == main_runs.holm_power(rows, n, draws=draws)          # seeded: the same every time


def test_the_holm_sizes_grow_by_whole_rounds_until_every_contrast_has_the_power():
    """Two independent twins at 36 pairs each: 0.903 alone but 0.890 after Holm, 0.932 at 42."""
    rows = [contrast("A3", 0.5, 1.0), contrast("A3", 0.5, 1.0, width=4)]
    start = {(2, 0.2): 36, (4, 0.2): 36}           # 90 % for each alone (WP12's formula gives 35)
    n, got = main_runs.holm_schedules(rows, start)
    assert n == {(2, 0.2): 42, (4, 0.2): 42} and min(got.values()) >= 0.9


def test_a_plan_is_written_once(tmp_path):
    out = tmp_path / "main_plan.json"
    out.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="written once"):
        main_runs.main(["plan", "--pilot", str(tmp_path), "--draw", str(tmp_path / "d.json"), "--out", str(out)])
