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
    "jcac_anchored": SystemSpec(
        "jcac", params={"anchor_moves": True},
        description="PolyForge with per-interval move clamps enforced across "
                    "coordination sweeps (PREREG_MOVE_CLAMP; the audit-caught "
                    "fix — the published jcac could move ±4 replicas / two "
                    "cache levels per interval via its second sweep)",
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
    "vtc_replica": SystemSpec(
        "vtc_replica", lru_eviction=True,
        description="VTC-replica: least-weighted-service-first fair pool division (OSDI '24 re-impl.)",
    ),
    "static": SystemSpec(
        "static", params={"overprovisioned": True}, lru_eviction=True,
        description="Static over-provisioned to peak: never violates, always pays",
    ),
    "gptcache": SystemSpec(
        "gptcache", lru_eviction=True,
        description="GPTCache posture: cache everything with LRU, HPA replicas",
    ),
    # --- Forecast ablation (Tier 2A): same controller, different eyes ----
    "jcac_persistence": SystemSpec(
        "jcac", params={"forecast_method": "persistence"},
        description="PolyForge with a persistence forecast: lookahead floor",
    ),
    "jcac_holt": SystemSpec(
        "jcac", params={"forecast_method": "holt"},
        description="PolyForge with damped Holt smoothing: noise-robust trend",
    ),
    "jcac_seasonal": SystemSpec(
        "jcac", params={"forecast_method": "seasonal"},
        description="PolyForge with online period detection: sees the next burst coming",
    ),
    # --- Self-calibration (Tier 2C): trusts the model only while it keeps
    # its promises; earns its keep under reconfiguration realism ----------
    "jcac_adaptive": SystemSpec(
        "jcac", params={"adaptive_capacity": True},
        description="PolyForge that learns effective capacity from realized-vs-projected feedback",
    ),
    # --- v2 (pre-registered: research/analysis/PREREG_V2.md) -------------
    "jcac_v2": SystemSpec(
        "jcac", params={"forecast_method": "holt", "adaptive_capacity": True},
        description="PolyForge v2: Holt forecast + capacity self-calibration (PREREG_V2.md)",
    ),
    # Iso-cost baselines (PREREG_V2 §5): the `isocost` marker is resolved
    # per run by harness.isocost from budgets derived off jcac_v2's realized
    # spend in the completed matrix_v2 (eval/baselines/isocost.py).
    "static_isocost": SystemSpec(
        "static", params={"isocost": "static"}, lru_eviction=True,
        description="Static posture pinned at PolyForge v2's realized per-cell budget (ceiling)",
    ),
    "cache_isocost": SystemSpec(
        "gptcache", params={"isocost": "cache"}, lru_eviction=True,
        description="GPTCache posture, cache pinned at PolyForge v2's realized cache spend (ceiling)",
    ),
    # --- Wave 2 chaos arms (PREREG_CHAOS_SIM.md). The chaos_* params are
    # engine settings consumed by sim_backend, never seen by the controller.
    # Windows are frozen in the prereg: planning outage starts at step 40
    # (post-warm-up, mid-run); 6 steps = 1 min of dead planner, 18 steps =
    # 3 min, which covers >= 1 full burst cycle of every periodic shape with
    # period <= 18 for every tenant phase. The replica kill hits scored
    # steps 60-62 (30 s of half-capacity before rescheduling). Baseline
    # chaos arms mirror their base systems' eviction accounting exactly.
    "jcac_outage_1m": SystemSpec(
        "jcac", params={"chaos_planner_outage": (40, 6)},
        description="PolyForge with its planner dead for 1 min mid-run (last-known-good hold)",
    ),
    "jcac_outage_3m": SystemSpec(
        "jcac", params={"chaos_planner_outage": (40, 18)},
        description="PolyForge with its planner dead for 3 min mid-run (last-known-good hold)",
    ),
    "hpa_outage_1m": SystemSpec(
        "hpa", lru_eviction=True, params={"chaos_planner_outage": (40, 6)},
        description="HPA with its control loop frozen 1 min (apiserver-throttling analog)",
    ),
    "jcac_kill50": SystemSpec(
        "jcac", params={"chaos_replica_kill": (60, 0.5, 3)},
        description="PolyForge under a 50% replica kill at steps 60-62, billing nominal",
    ),
    "hpa_kill50": SystemSpec(
        "hpa", lru_eviction=True, params={"chaos_replica_kill": (60, 0.5, 3)},
        description="HPA under the identical 50% replica kill",
    ),
    "keda_kill50": SystemSpec(
        "keda", lru_eviction=True, params={"chaos_replica_kill": (60, 0.5, 3)},
        description="KEDA under the identical 50% replica kill",
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
