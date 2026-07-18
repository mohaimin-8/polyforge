"""Coordination-gap measurement (PREREG_COORD_GAP.md): two-sweep
coordinate descent vs the exact joint optimum.

The controller docstring says the per-tenant move-blocked subproblem is
solved *exactly by enumeration*; cross-tenant coordination is two fixed
sweeps of coordinate descent in sorted-tenant order, with no bound. This
script measures that gap where the exact joint optimum is computable: the
full product lattice over small portfolios (N = 2, 3), with per-tenant
candidate projections memoized so the product walk is cheap.

Both solvers score the same global objective the coordinate step
implies — well-defined here because every instance pins
fairness_weight = 0.5, making the per-tenant fairness multiplier
uniform (a scoping condition, stated in the prereg):

    G = alpha * total_cost/COST_SCALE + beta * sum(log1p-excess terms)
        + gamma * (1 - Jain(satisfactions)) + SWITCH_PENALTY * switches

Feasibility (cluster cache/replica limits, per-tenant budget, move
clamps) is identical for both solvers; if no feasible joint point exists
the instance is discarded and counted (the CD fallback answers a
different question).

Run (from research/analysis):  python coordination_gap.py
Output: COORD_GAP.md + coord_gap.csv (per-instance rows).
"""

from __future__ import annotations

import csv
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))

import model  # noqa: E402
from controller import (  # noqa: E402
    COST_SCALE_USD,
    DELTA_REPLICAS,
    SWITCH_PENALTY,
    JCACController,
    Weights,
)
from model import (  # noqa: E402
    TIERS,
    Demand,
    TenantConfig,
    TenantState,
    apply_action,
    jain_index,
)
from controller import ClusterLimits  # noqa: E402

SEEDS = range(60)
SIZES = (2, 3)
WEIGHTS = Weights()  # deployed defaults: alpha 1, beta 2, gamma 0.5
LIMITS = ClusterLimits()  # deployed defaults: 4096 MB, 60 replicas
SLO_CYCLE = ("premium", "standard", "best-effort")

# Demand archetypes matching the five workload classes' kind profiles;
# rates drawn log-uniform inside each band so instances span idle -> busy.
ARCHETYPES = {
    "crud": {"crud_read": (2.0, 400.0), "crud_write": (1.0, 100.0)},
    "ai": {"chat": (0.2, 8.0), "embed": (0.5, 20.0)},
    "agentic": {"agent": (0.1, 3.0), "chat": (0.2, 4.0)},
    "mixed": {"crud_read": (2.0, 200.0), "chat": (0.2, 6.0)},
}


def draw_instance(rng: random.Random, n: int):
    configs, states, demands = {}, {}, {}
    for i in range(n):
        tid = f"t{i}"
        configs[tid] = TenantConfig(
            tenant_id=tid,
            slo_class=SLO_CYCLE[i % len(SLO_CYCLE)],
            hourly_budget_usd=5.0,
            fairness_weight=0.5,  # uniform: makes the global objective well-defined
        )
        states[tid] = TenantState(
            replicas=rng.randint(1, 6),
            cache_mb=rng.choice(model.CACHE_LEVELS_MB),
            tier=rng.choice(("small", "small", "mid", "large")),  # small-weighted
        )
        kind_bands = ARCHETYPES[rng.choice(list(ARCHETYPES))]
        rps = {}
        for kind, (lo, hi) in kind_bands.items():
            u = rng.random()
            rps[kind] = lo * (hi / lo) ** u
        demands[tid] = Demand(rps=rps, crud_base_ms=50.0)
    return configs, states, demands


def candidates(ctl: JCACController, tid: str, current: TenantState):
    """The exact candidate set _best_for_tenant enumerates, with its
    per-tenant feasibility (budget) applied; cluster limits are joint and
    checked in the product walk."""
    config = ctl.configs[tid]
    budget_per_step = config.hourly_budget_usd * model.CONTROL_INTERVAL_S / 3600.0
    out = []
    seen = set()
    for delta in DELTA_REPLICAS:
        for cache_mb in model.neighbor_cache_levels(current.cache_mb):
            for tier in TIERS:
                cand = apply_action(config, current, delta, cache_mb, tier)
                if cand in seen:
                    continue
                seen.add(cand)
                cost, viol, obj = ctl._project(tid, cand, ctl._horizons_cache[tid])
                if cost > budget_per_step:
                    continue
                switches = (
                    (cand.replicas != current.replicas)
                    + (cand.cache_mb != current.cache_mb)
                    + (cand.tier != current.tier)
                )
                out.append((cand, cost, viol, obj, switches))
    return out


def global_score(entries) -> float:
    total_cost = sum(e[1] for e in entries)
    total_obj = sum(e[3] for e in entries)
    switches = sum(e[4] for e in entries)
    fairness = 1.0 - jain_index([1.0 - e[2] for e in entries])
    return (WEIGHTS.alpha * total_cost / COST_SCALE_USD
            + WEIGHTS.beta * total_obj
            + WEIGHTS.gamma * fairness
            + SWITCH_PENALTY * switches)


def main() -> None:
    # --anchored: the PREREG_MOVE_CLAMP rerun — same frozen seeds, the
    # clamp-fixed controller. Writes *_anchored outputs, never touching
    # the original campaign record.
    anchored = "--anchored" in sys.argv
    rows = []
    for n in SIZES:
        for seed in SEEDS:
            rng = random.Random(1000 * n + seed)
            configs, states, demands = draw_instance(rng, n)
            ctl = JCACController(configs, weights=WEIGHTS, limits=LIMITS,
                                 anchor_moves=anchored)
            # One observation -> every forecast method returns a flat
            # horizon: forecast noise is excluded by construction.
            for tid, d in demands.items():
                ctl.forecasts[tid].observe(d)
            ctl._horizons_cache = {tid: ctl.forecasts[tid].horizon() for tid in configs}

            cand = {tid: candidates(ctl, tid, states[tid]) for tid in configs}
            if any(not c for c in cand.values()):
                rows.append({"n": n, "seed": seed, "status": "infeasible"})
                continue

            # Exact joint optimum over the product lattice.
            tids = sorted(configs)
            best = None
            for combo in itertools.product(*(cand[t] for t in tids)):
                if sum(e[0].cache_mb for e in combo) > LIMITS.cache_mb:
                    continue
                if sum(e[0].replicas for e in combo) > LIMITS.replicas:
                    continue
                score = global_score(combo)
                if best is None or score < best[0]:
                    best = (score, combo)
            if best is None:
                rows.append({"n": n, "seed": seed, "status": "infeasible"})
                continue

            # Coordinate-descent answer, scored on the same global objective.
            plans = ctl.plan(states, demands)
            by_state = {
                tid: next((e for e in cand[tid] if e[0] == plans[tid].state), None)
                for tid in tids
            }
            if any(e is None for e in by_state.values()):
                # CD landed on the shed-cost fallback outside the feasible
                # lattice — count it, do not score it.
                rows.append({"n": n, "seed": seed, "status": "cd_fallback"})
                continue
            cd_score = global_score([by_state[t] for t in tids])
            opt = best[0]
            gap = (cd_score - opt) / abs(opt) if opt else 0.0
            rows.append({
                "n": n, "seed": seed, "status": "ok",
                "cd_score": round(cd_score, 6), "opt_score": round(opt, 6),
                "rel_gap": round(gap, 6),
                "exact": int(abs(cd_score - opt) <= 1e-9),
            })
            print(f"n={n} seed={seed:2d} gap={gap:+.4%} "
                  f"{'EXACT' if rows[-1]['exact'] else ''}")

    suffix = "_anchored" if anchored else ""
    out_csv = Path(__file__).resolve().parent / f"coord_gap{suffix}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "n", "seed", "status", "cd_score", "opt_score", "rel_gap", "exact"])
        writer.writeheader()
        writer.writerows(rows)

    ok = [r for r in rows if r["status"] == "ok"]
    title = ("# Coordination gap (anchored controller) — clamp-fixed CD vs exact joint optimum"
             if anchored else
             "# Coordination gap — two-sweep coordinate descent vs exact joint optimum")
    lines = [title, ""]
    lines.append(("Campaign of `PREREG_MOVE_CLAMP.md` (anchored rerun over the same frozen seeds); "
                  if anchored else
                  "Campaign of `PREREG_COORD_GAP.md` (pushed before the run); ")
                 + f"mechanics in `coordination_gap.py`, rows in `coord_gap{suffix}.csv`.")
    lines.append("")
    lines.append("| N | instances | exact-match | median gap | p95 gap | max gap |")
    lines.append("|---|---|---|---|---|---|")
    verdicts = []
    for n in SIZES:
        sub = sorted(r["rel_gap"] for r in ok if r["n"] == n)
        if not sub:
            continue
        exact = sum(r["exact"] for r in ok if r["n"] == n)
        med = sub[len(sub) // 2]
        p95 = sub[min(len(sub) - 1, int(0.95 * (len(sub) - 1) + 0.5))]
        mx = sub[-1]
        verdicts.append((med, mx))
        lines.append(f"| {n} | {len(sub)} | {exact}/{len(sub)} | {med:.4%} "
                     f"| {p95:.4%} | {mx:.4%} |")
    skipped = [r for r in rows if r["status"] != "ok"]
    lines.append("")
    lines.append(f"Discarded instances (infeasible lattice or CD fallback): "
                 f"{len(skipped)} — listed in the CSV, per the prereg's handling rule.")
    lines.append("")
    h1 = all(med <= 0.01 and mx <= 0.05 for med, mx in verdicts)
    lines.append(f"**CG-H1 (median gap <= 1% and max gap <= 5% at every N): "
                 f"{'PASS' if h1 else 'FAIL'}.**")
    out_md = Path(__file__).resolve().parent / f"COORD_GAP{suffix.upper()}.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_md} and {out_csv}")


if __name__ == "__main__":
    main()
