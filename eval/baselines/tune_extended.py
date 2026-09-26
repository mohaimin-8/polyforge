"""Extended baseline tuning (audit 2026-09-26): do the tuned optima sit inside
their grids, and what are they worth against the treatment arm?

EXPLORATORY. Writes grids_extended/*.csv and NOTHING else: tuned.yaml, and
with it every published arm's behaviour, is untouched.

Every published W34 optimum sits on a grid edge (HPA, GPTCache and VTC at
rho = 0.3, KEDA at 2 rps/replica, concurrency at 0.5, FIRM at the lowest
learning rate). tune.py bounded the grids on the stated ground that J falls
monotonically as utilization targets fall and the limit is static
over-provisioning, "which the static baseline already covers". This sweep
tests that ground on the SAME slice, seeds and objective (score() is checked
against tune.score_combo before anything runs):

- each grid extended past its edge on the medium cluster;
- the replica controllers under the confirmatory comparators (hpa, keda) and
  vtc_replica at all three cluster sizes (the published tuning used medium
  only, where vtc_replica's grid equals hpa's row for row);
- jcac_converged and static scored on the same slice at every size, as the
  harness runs them (tune_arms.score_arm).

Run from eval/:  python baselines/tune_extended.py
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tune  # noqa: E402  (score_combo: the published slice, seeds and objective)
import tune_arms  # noqa: E402  (score_arm: an arm as sim_backend.execute runs it)

OUT = Path(__file__).resolve().parent / "grids_extended"

EXTENDED: dict[str, dict[str, list]] = {
    "hpa": {"target_rho": [0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4]},
    "keda": {"rps_per_replica": [0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]},
    "gptcache": {"target_rho": [0.005, 0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]},
    "firm": {"learning_rate": [0.005, 0.01, 0.02, 0.05, 0.1], "epsilon": [0.05, 0.1, 0.2],
             "w_slo": [4.0, 8.0, 16.0, 32.0, 64.0]},
    "concurrency": {"target_concurrency": [0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5],
                    "stable_intervals": [1, 3, 6, 9, 12]},
}
# The replica controllers the confirmatory comparators are built on, at the
# other two cluster sizes (the published tuning used medium only). On the
# medium slice vtc_replica's grid equals hpa's row for row: the pool never
# binds there, so VTC's fair-division rule never engaged.
PER_SIZE: dict[str, dict[str, list]] = {
    "hpa": {"target_rho": [0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6, 0.8]},
    "keda": {"rps_per_replica": [0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0]},
    "vtc_replica": {"target_rho": [0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6, 0.8]},
}
SIZES = ("small", "medium", "large")
# What the extended optima are worth: the treatment arm and the static
# over-provisioned baseline (which tune.py's bounds deferred to), scored as
# the harness runs them (tune_arms.score_arm: static carries its LRU charge)
# on the same slice, seeds and objective.
REFERENCE = ("jcac_converged", "static")


def score(controller: str, params: dict, cluster: str) -> dict:
    """tune.score_combo with the cluster as a parameter and J's three terms
    kept apart (main() checks it reproduces score_combo exactly)."""
    terms = {"cost_norm": 0.0, "violation": 0.0, "unfair": 0.0}
    n = len(tune.TUNING_CELLS) * tune.TUNING_REPS
    for wl, mix in tune.TUNING_CELLS:
        for rep in range(tune.TUNING_REPS):
            digest = hashlib.sha256(f"{wl}|{mix}|{rep}".encode()).hexdigest()
            seed = tune.TUNING_SEED + int(digest[:8], 16) % 10_000
            tenant_ids, buckets, configs, limits = tune.workloads.build(
                wl, mix, cluster, seed, tune.TUNING_STEPS)
            run_params = dict(params)
            if controller == "firm":
                run_params["seed"] = seed
            result = tune.simulate.run(
                controller, tenant_ids, buckets, configs=configs, limits=limits,
                collect_rows=False, controller_params=run_params,
                jitter_seed=seed ^ 0x5F3759DF)
            tenants = len(tenant_ids)
            terms["cost_norm"] += (result.total_cost_usd / max(1, result.steps * tenants)
                                   / tune.COST_SCALE_USD) / n
            terms["violation"] += result.mean_violation / n
            terms["unfair"] += (1.0 - result.mean_jain) / n
    j = tune.ALPHA * terms["cost_norm"] + tune.BETA * terms["violation"] + tune.GAMMA * terms["unfair"]
    return {"mean_objective": round(j, 5), **{k: round(v, 5) for k, v in terms.items()}}


def sweep(controller: str, grid: dict[str, list], cluster: str) -> list[dict]:
    names = sorted(grid)
    rows = []
    for values in itertools.product(*(grid[n] for n in names)):
        params = dict(zip(names, values))
        row = {**params, **score(controller, params, cluster)}
        rows.append(row)
        print(f"  {controller}@{cluster} {row}", flush=True)
    return rows


def write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(exist_ok=True)
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    check = score("hpa", {"target_rho": 0.3}, "medium")["mean_objective"]
    published = round(tune.score_combo("hpa", {"target_rho": 0.3}), 5)
    if check != published:
        raise SystemExit(f"score() drifted from tune.score_combo: {check} != {published}")
    for controller, grid in EXTENDED.items():
        write(controller, sweep(controller, grid, "medium"))
    for cluster in SIZES:
        if cluster != "medium":
            for controller, grid in PER_SIZE.items():
                write(f"{controller}_{cluster}", sweep(controller, grid, cluster))
        rows = []
        for arm in REFERENCE:
            rows.append({"arm": arm, **tune_arms.score_arm(arm, {}, cluster)})
            print(f"  {arm}@{cluster} {rows[-1]}", flush=True)
        write(f"reference_{cluster}", rows)
    write("vtc_replica", sweep("vtc_replica", PER_SIZE["vtc_replica"], "medium"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
