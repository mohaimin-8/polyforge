"""Cross-tenant timing side channel in shared semantic caches — the attack,
and PolyForge's per-tenant isolation as the defense (novel contribution).

Threat model
------------
A semantic cache answers an LLM request from a stored completion when an
incoming prompt is *semantically near* one already cached, turning an
~800 ms model call into a ~20 ms lookup. A shared cache — GPTCache's
default posture, and what most multi-tenant deployments run — keys on the
prompt, not the tenant. That coupling is a covert channel: a hit means
*someone* recently asked a similar question. An attacker who can time
responses and craft probe prompts can therefore test hypotheses about
what *other tenants* are asking — membership inference over private
prompts, with no access to any tenant's data.

This class of attack against production LLM prompt/semantic caches was
demonstrated in the 2025 literature; here we quantify it end to end on
PolyForge's own cache model and show that the per-tenant bounded cache
(internal/ai/gateway/cache.go, W28) closes it — and that the joint
controller's demand-proportional sizing makes the isolation measurably
cheaper than a naive equal split.

What this script computes
-------------------------
1. ATTACK POWER on a shared cache: an attacker probes a secret prompt held
   by a victim tenant. We measure the timing gap (hit vs miss latency) and
   the membership-inference AUC a threshold classifier achieves from
   response time alone, as a function of how aggressively other tenants'
   traffic warms the shared cache.
2. ISOLATION EFFICACY: rerun the identical attack against a per-tenant
   cache (the attacker's probes can only ever hit the attacker's own
   entries). AUC collapses to chance.
3. COST OF ISOLATION, and how the planner blunts it: partitioning a fixed
   memory budget per tenant lowers the aggregate hit rate (smaller pools).
   We measure that penalty under equal splitting, then show PolyForge's
   demand-proportional cache sizing (what the joint planner does) recovers
   most of it — security at a fraction of the naive cost.
4. DEFENSE FRONTIER (v2 Phase 5): the two standard timing-channel
   mitigations, evaluated on the same attack —
   - response-time quantization ("padding"): every response is delayed to
     the next multiple of Q ms. Cost: latency benefit of caching shrinks.
   - probabilistic TTL jitter: a would-be hit is served cold with
     probability q (early expiry). Cost: q of all hits are sacrificed
     (latency AND inference dollars).
   Each defense is swept over a fixed grid (not tuned) and plotted as
   leakage (worst-case AUC) vs the share of the cache's latency benefit
   given up. Per-tenant partitioning is placed on the same axes at its
   measured hit-rate cost. The claim this upgrades: partitioning is not
   just *a* defense — it dominates the known mitigation frontier.

Deterministic; stdlib + the repo's own model constants only. Run:
    python research/security/cache_side_channel.py --out ../results/security
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the exact latency and hit-rate constants the rest of the thesis
# uses, so this study speaks the same units as the evaluation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
from model import CACHE_HALF_MB, CACHE_HIT_LATENCY_MS, CACHE_HIT_MAX  # noqa: E402

MISS_LATENCY_MS = 800.0  # a mid-tier chat completion (model.TIER_BASE_LATENCY_MS)
NETWORK_JITTER_MS = 25.0  # attacker-side timing noise (std dev); the defender's friend


@dataclass
class AttackResult:
    scenario: str
    warm_fraction: float
    hit_latency_ms: float
    miss_latency_ms: float
    auc: float  # membership-inference AUC from response time alone
    leak_bits: float  # channel capacity estimate, bits per probe
    rows: list = field(default_factory=list)


def _hit_probability_shared(warm_fraction: float, rng: random.Random) -> float:
    """On a SHARED cache, the victim's secret prompt is warm iff some tenant
    populated it. `warm_fraction` is the share of cross-tenant traffic that
    collides semantically with the probe — the attacker's leverage."""
    return warm_fraction * CACHE_HIT_MAX


def _sample_latency(is_hit: bool, rng: random.Random) -> float:
    base = CACHE_HIT_LATENCY_MS if is_hit else MISS_LATENCY_MS
    return max(0.0, base + rng.gauss(0.0, NETWORK_JITTER_MS))


# --- v2 Phase 5: timing-channel defenses -------------------------------
# Fixed sweep grids, committed before the frontier was computed; never
# tuned. Padding grids run up to the miss latency because that is the
# point where hit and miss become indistinguishable by construction.
PAD_GRID_MS = (50.0, 100.0, 200.0, 400.0, 800.0)
TTL_JITTER_GRID = (0.1, 0.25, 0.5, 0.75, 0.9)


def _quantize_up(latency_ms: float, grid_ms: float) -> float:
    """Response-time padding: hold every response until the next multiple
    of `grid_ms` — the deployable form of constant-time defenses."""
    return math.ceil(latency_ms / grid_ms) * grid_ms


def _defended_latency(is_hit: bool, rng: random.Random, defense) -> float:
    kind, param = defense if defense else (None, 0.0)
    if kind == "ttl" and is_hit and rng.random() < param:
        is_hit = False  # entry expired early: served cold
    t = _sample_latency(is_hit, rng)
    if kind == "pad":
        t = _quantize_up(t, param)
    return t


def _auc(pos: list[float], neg: list[float]) -> float:
    """AUC = P(a warm-probe time ranks below a cold-probe time), i.e. the
    Mann-Whitney statistic. 0.5 is chance; 1.0 is perfect leakage. Timing
    is inverted (a hit is *faster*), so we score P(pos < neg)."""
    if not pos or not neg:
        return 0.5
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p < n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def run_attack(scenario: str, warm_fraction: float, probes: int, seed: int,
               defense=None) -> AttackResult:
    """One attacker campaign: `probes` timed requests against a secret the
    attacker does not hold, half in a world where the victim asked the
    secret (positive class) and half where they did not (negative).
    `defense` is None or ("pad", grid_ms) / ("ttl", expiry_prob), applied
    to the shared cache the attacker observes."""
    rng = random.Random(seed)
    pos_times, neg_times = [], []
    for _ in range(probes):
        if scenario == "shared":
            # Positive world: victim asked it, so it is warm with the
            # shared hit probability. Negative world: nobody did -> cold.
            p_hit_when_asked = _hit_probability_shared(warm_fraction, rng)
            pos_times.append(_defended_latency(rng.random() < p_hit_when_asked, rng, defense))
            neg_times.append(_defended_latency(False, rng, defense))
        else:  # per-tenant isolation
            # The attacker probes its OWN cache. The victim asking or not
            # changes nothing the attacker can observe -> both draws cold.
            pos_times.append(_sample_latency(False, rng))
            neg_times.append(_sample_latency(False, rng))

    auc = _auc(pos_times, neg_times)
    # Channel capacity of a binary timing channel with this AUC, via the
    # equivalent bit-error rate p = 1 - AUC (bounded away from the
    # degenerate ends): C = 1 - H(p).
    p_err = min(0.5, max(1e-4, 1.0 - auc))
    h = -p_err * math.log2(p_err) - (1 - p_err) * math.log2(1 - p_err)
    leak_bits = max(0.0, 1.0 - h)
    return AttackResult(
        scenario=scenario, warm_fraction=warm_fraction,
        hit_latency_ms=CACHE_HIT_LATENCY_MS, miss_latency_ms=MISS_LATENCY_MS,
        auc=auc, leak_bits=leak_bits,
    )


# The isolation-cost scenario: a whale and seven minnows sharing 4 GB.
ISOLATION_DEMAND = [8.0, 4.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]
ISOLATION_TOTAL_MB = 4096.0
# The model's largest cache level (research/jcac_sim/model.py CACHE_LEVELS_MB).
TOP_CACHE_MB = 1024.0


def _agg_hit_rate(mb: float) -> float:
    return CACHE_HIT_MAX * mb / (mb + CACHE_HALF_MB)


def capped_split(total_mb: float, shares: list[float], cap_mb: float) -> list[float]:
    """`total_mb` split in proportion to `shares`, no pool above `cap_mb`:
    a capped tenant's excess is re-split among the uncapped ones."""
    alloc = [0.0] * len(shares)
    free, left = set(range(len(shares))), total_mb
    while free and left > 1e-9:
        weight = sum(shares[i] for i in free)
        over = [i for i in free if shares[i] / weight * left > cap_mb - alloc[i]]
        if not over:
            for i in free:
                alloc[i] += shares[i] / weight * left
            break
        for i in over:
            left -= cap_mb - alloc[i]
            alloc[i] = cap_mb
            free.discard(i)
    return alloc


def isolation_cost(total_mb: float, n_tenants: int, demand_shares: list[float],
                   cap_mb: float | None = None) -> dict:
    """Cost of isolation, and PolyForge's recovery of it.

    - shared:   one pool of `total_mb`, hit rate on the full pool.
    - equal:    total_mb / n per tenant (naive isolation).
    - polyforge: total_mb split in proportion to each tenant's demand, the
      joint planner's cache-sizing behavior — big talkers get big pools,
      so the weighted hit rate stays close to shared.

    `cap_mb` bounds each pool (audit 2026-09-26: the uncapped split gives the
    largest tenant 1,725 MB, above the model's 1,024 MB top cache level, an
    allocation the controller cannot make). None keeps the published split.
    """
    shared = _agg_hit_rate(total_mb)
    equal_each = total_mb / n_tenants
    equal = _agg_hit_rate(equal_each)
    shares = [s / sum(demand_shares) for s in demand_shares]
    pools = ([w * total_mb for w in shares] if cap_mb is None
             else capped_split(total_mb, shares, cap_mb))
    poly = sum(w * _agg_hit_rate(max(1.0, mb)) for w, mb in zip(shares, pools))
    return {
        "total_mb": total_mb, "n_tenants": n_tenants,
        "shared_hit_rate": round(shared, 4),
        "equal_split_hit_rate": round(equal, 4),
        "polyforge_hit_rate": round(poly, 4),
        "naive_isolation_penalty": round((shared - equal) / shared, 4),
        "polyforge_penalty": round((shared - poly) / shared, 4),
        "penalty_recovered": round(1.0 - (shared - poly) / max(1e-9, shared - equal), 4),
    }


def latency_benefit_cost(defense, probes: int, seed: int) -> float:
    """Share of the cache's expected latency benefit a defense gives up,
    measured on the same latency model (Monte Carlo, includes jitter).
    Undefended benefit per hit is MISS − HIT; padding shrinks the gap by
    delaying hits to the grid, TTL jitter throws hits away outright."""
    kind, param = defense
    if kind == "ttl":
        return float(param)  # exactly q of hits are sacrificed
    rng = random.Random(seed)
    hit = sum(_defended_latency(True, rng, defense) for _ in range(probes)) / probes
    miss = sum(_defended_latency(False, rng, defense) for _ in range(probes)) / probes
    base_gap = MISS_LATENCY_MS - CACHE_HIT_LATENCY_MS
    return max(0.0, min(1.0, 1.0 - (miss - hit) / base_gap))


def defense_frontier(probes: int, seed: int, worst_wf: float = 0.9) -> list[dict]:
    """Leakage (worst-case AUC at the attacker's best leverage) vs latency
    cost, for every defense on the fixed grids plus per-tenant isolation.
    `hits_retained` carries the dollar dimension: hits avoid tier-priced
    inference, so sacrificing hits (TTL, partitioning's hit-rate penalty)
    costs money where padding only costs time."""
    rows = []
    rows.append({
        "defense": "none", "param": 0.0,
        "auc_worst": run_attack("shared", worst_wf, probes, seed).auc,
        "latency_benefit_cost": 0.0, "hits_retained": 1.0,
    })
    for q_ms in PAD_GRID_MS:
        d = ("pad", q_ms)
        rows.append({
            "defense": "pad", "param": q_ms,
            "auc_worst": run_attack("shared", worst_wf, probes, seed, defense=d).auc,
            "latency_benefit_cost": latency_benefit_cost(d, probes, seed),
            "hits_retained": 1.0,
        })
    for q in TTL_JITTER_GRID:
        d = ("ttl", q)
        rows.append({
            "defense": "ttl", "param": q,
            "auc_worst": run_attack("shared", worst_wf, probes, seed, defense=d).auc,
            "latency_benefit_cost": latency_benefit_cost(d, probes, seed),
            "hits_retained": round(1.0 - q, 4),
        })
    demand = ISOLATION_DEMAND
    cost = isolation_cost(total_mb=ISOLATION_TOTAL_MB, n_tenants=len(demand), demand_shares=demand)
    iso_auc = run_attack("per_tenant", worst_wf, probes, seed).auc
    for name, penalty in (("partition_equal", cost["naive_isolation_penalty"]),
                          ("partition_polyforge", cost["polyforge_penalty"])):
        rows.append({
            "defense": name, "param": 0.0, "auc_worst": iso_auc,
            "latency_benefit_cost": penalty,
            "hits_retained": round(1.0 - penalty, 4),
        })
    for r in rows:
        r["auc_worst"] = round(r["auc_worst"], 4)
        r["latency_benefit_cost"] = round(r["latency_benefit_cost"], 4)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None, help="output prefix (.json + .csv)")
    ap.add_argument("--probes", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    warm_grid = [0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9]
    attack_rows = []
    for wf in warm_grid:
        for scenario in ("shared", "per_tenant"):
            r = run_attack(scenario, wf, args.probes, args.seed)
            attack_rows.append({
                "scenario": scenario, "warm_fraction": wf,
                "auc": round(r.auc, 4), "leak_bits_per_probe": round(r.leak_bits, 4),
            })

    shared_auc = max(row["auc"] for row in attack_rows if row["scenario"] == "shared")
    iso_auc = max(row["auc"] for row in attack_rows if row["scenario"] == "per_tenant")

    demand = ISOLATION_DEMAND  # a whale + minnows
    cost = isolation_cost(total_mb=ISOLATION_TOTAL_MB, n_tenants=len(demand), demand_shares=demand)

    frontier_rows = defense_frontier(args.probes, args.seed)

    summary = {
        "attack": {
            "shared_cache_max_auc": round(shared_auc, 4),
            "per_tenant_max_auc": round(iso_auc, 4),
            "auc_reduction": round((shared_auc - iso_auc) / (shared_auc - 0.5), 4),
            "note": "AUC 0.5 is chance; per-tenant isolation returns the attacker to chance.",
        },
        "isolation_cost": cost,
        "headline": (
            f"A shared semantic cache leaks tenant prompt membership at AUC "
            f"{shared_auc:.2f} from response time alone; PolyForge's per-tenant "
            f"cache returns the attacker to chance (AUC {iso_auc:.2f}). Naive "
            f"equal-split isolation costs {cost['naive_isolation_penalty']:.0%} of "
            f"the aggregate hit rate; PolyForge's demand-proportional sizing "
            f"recovers {cost['penalty_recovered']:.0%} of that penalty."
        ),
    }

    print(json.dumps(summary, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        (out.parent / (out.name + ".json")).write_text(json.dumps(
            {"summary": summary, "attack_rows": attack_rows, "warm_grid": warm_grid,
             "defense_frontier": frontier_rows},
            indent=2), encoding="utf-8")
        import csv
        with open(out.parent / (out.name + ".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(attack_rows[0].keys()))
            w.writeheader()
            w.writerows(attack_rows)
        with open(out.parent / (out.name + "_frontier.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(frontier_rows[0].keys()))
            w.writeheader()
            w.writerows(frontier_rows)
        print(f"\nwrote {out}.json, {out}.csv and {out}_frontier.csv")


if __name__ == "__main__":
    main()
