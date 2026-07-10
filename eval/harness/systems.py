"""System registry: PolyForge, the five W34 baselines, and the W36
ablations, each mapped to concrete simulator settings.

Eviction accounting: PolyForge's semantic cache evicts cost-aware (W28);
every baseline that caches evicts LRU. The W28 benchmark measured LRU's
inference spend per request against cost-aware at two cache capacities
(research/results/eviction_comparison.csv); the geometric mean of those
ratios is the `miss_cost_factor` applied to LRU systems' tier spend. The
factor is loaded from the CSV so the provenance is checkable; the fallback
constant is the value computed from the committed CSV.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from . import REPO_ROOT

EVICTION_CSV = REPO_ROOT / "research" / "results" / "eviction_comparison.csv"
LRU_MISS_COST_FACTOR_FALLBACK = 1.458  # geometric mean of committed W28 ratios


def lru_miss_cost_factor() -> float:
    """cost-per-request(LRU) / cost-per-request(cost-aware), geometric mean
    over the cache capacities the W28 study measured."""
    try:
        by_capacity: dict[str, dict[str, float]] = {}
        with open(EVICTION_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["policy"] in ("lru", "costaware_w28"):
                    by_capacity.setdefault(row["capacity_entries"], {})[row["policy"]] = float(
                        row["cost_per_request_usd"]
                    )
        ratios = [
            caps["lru"] / caps["costaware_w28"]
            for caps in by_capacity.values()
            if "lru" in caps and "costaware_w28" in caps and caps["costaware_w28"] > 0
        ]
        if not ratios:
            return LRU_MISS_COST_FACTOR_FALLBACK
        return math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    except (OSError, KeyError, ValueError):
        return LRU_MISS_COST_FACTOR_FALLBACK


TUNED_PATH = Path(__file__).resolve().parents[1] / "baselines" / "tuned.yaml"


def tuned_params() -> dict:
    """Best-of-grid controller parameters from the W34 tuning sweep
    (eval/baselines/tune.py writes tuned.yaml). Before the sweep has run,
    every controller uses its own documented defaults."""
    if not TUNED_PATH.exists():
        return {}
    import yaml

    return yaml.safe_load(TUNED_PATH.read_text(encoding="utf-8")) or {}


def global_mix_transform(demands):
    """The `-classifier` ablation: the controller keeps each tenant's
    request *volume* but sees the cluster-wide average request *mix*
    instead of the tenant's own. That is exactly what is lost without
    per-tenant workload classification."""
    from model import Demand  # sim import, resolved via harness sys.path

    totals: dict[str, float] = {}
    grand = 0.0
    for d in demands.values():
        for kind, rate in d.rps.items():
            totals[kind] = totals.get(kind, 0.0) + rate
            grand += rate
    if grand <= 0.0:
        return demands
    shares = {kind: rate / grand for kind, rate in totals.items()}
    out = {}
    for tid, d in demands.items():
        volume = d.total_rps()
        out[tid] = Demand(
            rps={kind: share * volume for kind, share in shares.items()},
            crud_base_ms=d.crud_base_ms,
        )
    return out


class SystemSpec:
    def __init__(
        self,
        controller: str,
        params: dict | None = None,
        gamma: float | None = None,
        lru_eviction: bool = False,
        blind_classifier: bool = False,
        seeded: bool = False,
        description: str = "",
    ):
        self.controller = controller
        self.params = params or {}
        self.gamma = gamma  # JCAC fairness weight override (None = default)
        self.lru_eviction = lru_eviction
        self.blind_classifier = blind_classifier
        self.seeded = seeded  # controller takes a per-run seed (FIRM)
        self.description = description


SYSTEMS: dict[str, SystemSpec] = {
    # --- PolyForge -----------------------------------------------------
    "jcac": SystemSpec(
        "jcac",
        description="PolyForge: joint MPC over replicas, cache, tier (W30-32)",
    ),
    # --- W34 baselines ---------------------------------------------------
    "hpa": SystemSpec(
        "hpa", lru_eviction=True,
        description="Vanilla K8s HPA on utilization; cache and tier fixed",
    ),
    "keda": SystemSpec(
        "keda", lru_eviction=True,
        description="KEDA event-driven scaler on RPS; cache and tier fixed",
    ),
    "firm": SystemSpec(
        "firm", lru_eviction=True, seeded=True,
        description="FIRM-replica: RL replica controller (OSDI '20 re-impl.)",
    ),
    "static": SystemSpec(
        "static", params={"overprovisioned": True}, lru_eviction=True,
        description="Static over-provisioned to peak: never violates, always pays",
    ),
    "gptcache": SystemSpec(
        "gptcache", lru_eviction=True,
        description="GPTCache posture: cache everything with LRU, HPA replicas",
    ),
    # --- W36 ablations (PolyForge minus one contribution) ----------------
    "jcac_noclassifier": SystemSpec(
        "jcac", blind_classifier=True,
        description="PolyForge minus workload classifier: plans on global mix",
    ),
    "jcac_nojoint": SystemSpec(
        "layered",
        description="PolyForge minus joint controller: per-layer local controllers",
    ),
    "jcac_noeviction": SystemSpec(
        "jcac", lru_eviction=True,
        description="PolyForge minus cost-aware eviction: LRU miss economics",
    ),
    "jcac_nofairness": SystemSpec(
        "jcac", gamma=0.0,
        description="PolyForge minus fairness objective (γ=0, no interference term)",
    ),
}
