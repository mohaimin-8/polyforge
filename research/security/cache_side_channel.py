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


def run_attack(scenario: str, warm_fraction: float, probes: int, seed: int) -> AttackResult:
    """One attacker campaign: `probes` timed requests against a secret the
    attacker does not hold, half in a world where the victim asked the
    secret (positive class) and half where they did not (negative)."""
    rng = random.Random(seed)
    pos_times, neg_times = [], []
    for _ in range(probes):
        if scenario == "shared":
            # Positive world: victim asked it, so it is warm with the
            # shared hit probability. Negative world: nobody did -> cold.
            p_hit_when_asked = _hit_probability_shared(warm_fraction, rng)
            pos_times.append(_sample_latency(rng.random() < p_hit_when_asked, rng))
            neg_times.append(_sample_latency(False, rng))
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


def _agg_hit_rate(mb: float) -> float:
    return CACHE_HIT_MAX * mb / (mb + CACHE_HALF_MB)


def isolation_cost(total_mb: float, n_tenants: int, demand_shares: list[float]) -> dict:
    """Cost of isolation, and PolyForge's recovery of it.

    - shared:   one pool of `total_mb`, hit rate on the full pool.
    - equal:    total_mb / n per tenant (naive isolation).
    - polyforge: total_mb split in proportion to each tenant's demand, the
      joint planner's cache-sizing behavior — big talkers get big pools,
      so the weighted hit rate stays close to shared.
    """
    shared = _agg_hit_rate(total_mb)
    equal_each = total_mb / n_tenants
    equal = _agg_hit_rate(equal_each)
    shares = [s / sum(demand_shares) for s in demand_shares]
    poly = sum(w * _agg_hit_rate(max(1.0, w * total_mb)) for w in shares)
    return {
        "total_mb": total_mb, "n_tenants": n_tenants,
        "shared_hit_rate": round(shared, 4),
        "equal_split_hit_rate": round(equal, 4),
        "polyforge_hit_rate": round(poly, 4),
        "naive_isolation_penalty": round((shared - equal) / shared, 4),
        "polyforge_penalty": round((shared - poly) / shared, 4),
        "penalty_recovered": round(1.0 - (shared - poly) / max(1e-9, shared - equal), 4),
    }


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

    demand = [8.0, 4.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]  # a whale + minnows
    cost = isolation_cost(total_mb=4096.0, n_tenants=len(demand), demand_shares=demand)

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
            {"summary": summary, "attack_rows": attack_rows, "warm_grid": warm_grid},
            indent=2), encoding="utf-8")
        import csv
        with open(out.parent / (out.name + ".csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(attack_rows[0].keys()))
            w.writeheader()
            w.writerows(attack_rows)
        print(f"\nwrote {out}.json and {out}.csv")


if __name__ == "__main__":
    main()
