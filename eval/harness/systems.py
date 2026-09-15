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


def miss_cost_factor_for(policy: str = "lru") -> float:
    """cost-per-request(`policy`) / cost-per-request(cost-aware), geometric
    mean over the cache capacities the W28 study measured.

    PREREG_EVICTION_PARITY §Design: the published headline charges every
    baseline the **LRU** ratio (1.4581), which is the most favorable of the
    four comparators in the committed CSV — GDSF actually *beats* the
    cost-aware policy at both capacities (factor 0.978, i.e. it would run
    against the proposal) and ARC gives 1.337. The comparator is therefore a
    reported sensitivity band, not a single constant. `lru` remains the
    default so every published arm reproduces bit-identically (R4).
    """
    try:
        by_capacity: dict[str, dict[str, float]] = {}
        with open(EVICTION_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["policy"] in (policy, "costaware_w28"):
                    by_capacity.setdefault(row["capacity_entries"], {})[row["policy"]] = float(
                        row["cost_per_request_usd"]
                    )
        ratios = [
            caps[policy] / caps["costaware_w28"]
            for caps in by_capacity.values()
            if policy in caps and "costaware_w28" in caps and caps["costaware_w28"] > 0
        ]
        if not ratios:
            return LRU_MISS_COST_FACTOR_FALLBACK
        return math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    except (OSError, KeyError, ValueError):
        return LRU_MISS_COST_FACTOR_FALLBACK


def lru_miss_cost_factor() -> float:
    """The published default: the LRU comparator. Kept as a named function
    because the committed campaigns and `reproduce.py` call it by this name."""
    return miss_cost_factor_for("lru")


def eviction_sensitivity_band() -> dict[str, float]:
    """The factor under every comparator in the committed CSV, plus the
    no-charge control. Reported alongside the headline delta so the reader
    sees how much of it is eviction accounting (PREREG_EVICTION_PARITY EP-H2)."""
    return {
        "none": 1.0,
        "gdsf": miss_cost_factor_for("gdsf"),
        "arc": miss_cost_factor_for("arc"),
        "lru": miss_cost_factor_for("lru"),
    }


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
        knob_freeze: frozenset[str] = frozenset(),
        static_cache_mb: int | None = None,
        beta: float | None = None,
        description: str = "",
    ):
        self.controller = controller
        self.params = params or {}
        self.gamma = gamma  # JCAC fairness weight override (None = default)
        self.lru_eviction = lru_eviction
        self.blind_classifier = blind_classifier
        self.seeded = seeded  # controller takes a per-run seed (FIRM)
        # Which knobs are pinned at the initial world, from the subset
        # {"replicas","cache","tier"}. The live-plane cache-only / tier-only
        # ablations (PREREG_WAVE4_LIVE_PLANE.md §Arms) freeze two knobs so the
        # joint controller re-optimizes the third alone; the sim backend
        # applies this to each TenantConfig, the operator does it via the
        # Policy CRD bounds. Empty = every knob free (the published default).
        self.knob_freeze = knob_freeze
        # Initial cache size for this arm's tenants, in MB. The reactive
        # baselines carry `state.cache_mb` forward unchanged (baselines.py),
        # so setting the initial value pins the cache there for the whole run
        # — that is what makes a *competently pre-sized* comparator possible
        # (PREREG_EVICTION_PARITY §Design: hpa_fair / keda_fair at 512 MB).
        # None keeps the engine default, so every published arm is untouched.
        self.static_cache_mb = static_cache_mb
        # SLO weight in the controller's objective (`Weights.beta`, default
        # 2.0). The objective is alpha*cost/COST_SCALE + beta*log1p(excess) +
        # gamma*fairness: cost enters linearly and overshoot logarithmically,
        # so beta selects a point on the cost/SLO frontier. Raising it buys
        # attainment with money. PREREG_VIOLATION_PARITY sweeps it to reach the
        # fair comparators' violation; None keeps the published point, so every
        # committed arm replays bit-identically (R4).
        self.beta = beta
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
    # --- Risk-aware MPC frontier (PREREG_RISK_MPC) -----------------------
    # Same controller as jcac_anchored, planning against a demand *quantile*
    # from its own forecast residuals instead of the point forecast. Sweeping
    # q traces a cost/violation frontier: jcac_anchored is the q=point-forecast
    # end, jcac_q95 the conservative end. Nothing else differs.
    "jcac_q70": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.70},
        description="PolyForge, risk-aware MPC at demand quantile 0.70 (PREREG_RISK_MPC)",
    ),
    "jcac_q80": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.80},
        description="PolyForge, risk-aware MPC at demand quantile 0.80 (PREREG_RISK_MPC)",
    ),
    "jcac_q90": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.90},
        description="PolyForge, risk-aware MPC at demand quantile 0.90 (PREREG_RISK_MPC; "
                    "the pre-declared RQ-H1 operating point)",
    ),
    "jcac_q95": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.95},
        description="PolyForge, risk-aware MPC at demand quantile 0.95 (PREREG_RISK_MPC)",
    ),
    # --- Budget-corrected risk frontier (PREREG_RISK_BUDGET) --------------
    # The one changed factor after the RESULTS_RISK null: capacity is sized
    # at the risk quantile, but cost is projected and the budget checked at
    # the POINT forecast — you are billed for the demand that arrives, not
    # the demand you provisioned against. Everything else identical to the
    # jcac_q* arms above (which stay untouched as the published null).
    "jcac_q70c": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.70,
                        "risk_cost_at_point": True},
        description="PolyForge, budget-corrected risk MPC at q=0.70 (PREREG_RISK_BUDGET)",
    ),
    "jcac_q80c": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.80,
                        "risk_cost_at_point": True},
        description="PolyForge, budget-corrected risk MPC at q=0.80 (PREREG_RISK_BUDGET)",
    ),
    "jcac_q90c": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.90,
                        "risk_cost_at_point": True},
        description="PolyForge, budget-corrected risk MPC at q=0.90 (PREREG_RISK_BUDGET; "
                    "the pre-declared RB operating point)",
    ),
    "jcac_q95c": SystemSpec(
        "jcac", params={"anchor_moves": True, "risk_quantile": 0.95,
                        "risk_cost_at_point": True},
        description="PolyForge, budget-corrected risk MPC at q=0.95 (PREREG_RISK_BUDGET)",
    ),
    # --- V-series order-permutation arms (PREREG_ORDER_PERMUTATION.md) -----
    # The coordinate-descent sweep is first-come-first-served on the shared
    # cluster caps, and the published sorted() order is confounded with
    # priority class: tenant ids are assigned by slot and every non-uniform
    # mix puts premium/whale tenants in the low slots. These arms re-run the
    # identical controller under fixed alternative permutations, so the
    # spread across them measures how much of the published fairness result
    # is a property of the naming scheme. jcac itself is untouched.
    **{
        f"jcac_perm{s}": SystemSpec(
            "jcac", params={"tenant_order_seed": s},
            description=f"PolyForge with sweep order permuted under seed {s} "
                        "(PREREG_ORDER_PERMUTATION; controller otherwise identical)",
        )
        for s in (1, 2, 3, 4, 5)
    },
    # --- V-series eviction-parity arms (PREREG_EVICTION_PARITY.md §Design) --
    # The audited cost-accounting confound: the published baselines are all
    # charged a 1.4581x LRU inference penalty that jcac does not pay, and are
    # pinned at the 128 MB initial cache they can never move. These arms drop
    # the penalty AND pre-size the cache at the level jcac itself converges to
    # (512 MB), which is what a competent operator would deploy. They are the
    # comparator EP-H1 is scored against. The published hpa/keda arms are
    # untouched and still carry the factor, so the record stands as published.
    "hpa_fair": SystemSpec(
        "hpa", static_cache_mb=512,
        description="Reactive HPA replica control with a competently pre-sized "
                    "512 MB cache and no LRU cost penalty (PREREG_EVICTION_PARITY "
                    "EP-H1 primary comparator)",
    ),
    "keda_fair": SystemSpec(
        "keda", static_cache_mb=512,
        description="Event-driven KEDA replica control with a competently "
                    "pre-sized 512 MB cache and no LRU cost penalty "
                    "(PREREG_EVICTION_PARITY EP-H1)",
    ),
    # PREREG_VIOLATION_PARITY: the frozen geometric beta ladder. WP1 compared
    # jcac at 0.5134 mean excess against hpa_fair at 0.008818 -- a 58x severity
    # gap -- and read off a cost difference, which is not the "cost at
    # violation parity" comparison the thesis claims. These arms buy attainment
    # with money so the comparison can be made at matched severity. The
    # published `jcac` (beta 2.0) is untouched.
    "jcac_b4": SystemSpec(
        "jcac", beta=4.0,
        description="PolyForge at SLO weight 4 (PREREG_VIOLATION_PARITY ladder)"),
    "jcac_b8": SystemSpec(
        "jcac", beta=8.0,
        description="PolyForge at SLO weight 8 (PREREG_VIOLATION_PARITY ladder)"),
    "jcac_b16": SystemSpec(
        "jcac", beta=16.0,
        description="PolyForge at SLO weight 16 (PREREG_VIOLATION_PARITY ladder)"),
    "jcac_b32": SystemSpec(
        "jcac", beta=32.0,
        description="PolyForge at SLO weight 32 (PREREG_VIOLATION_PARITY ladder)"),
    "jcac_b64": SystemSpec(
        "jcac", beta=64.0,
        description="PolyForge at SLO weight 64 (PREREG_VIOLATION_PARITY ladder)"),
    # PREREG_BUDGET_PARITY: the two directions that bracket the confound.
    # controller.py:647 rejects over-budget candidates before the objective;
    # baselines.py:97-105 has no affordability check at all, so jcac is the
    # only arm solving "best SLO within budget". These make the arms solve the
    # same problem, in both directions, so neither can be called the unfair one.
    "hpa_budget": SystemSpec(
        "hpa_budget", lru_eviction=False, static_cache_mb=512,
        description="Reactive HPA under the SAME per-tenant Budget CRD filter "
                    "the proposal obeys, fair cache posture (PREREG_BUDGET_PARITY "
                    "BP-H1a: the like-for-like cost comparator)"),
    "keda_budget": SystemSpec(
        "keda_budget", lru_eviction=False, static_cache_mb=512,
        description="Event-driven KEDA under the same budget filter and fair "
                    "cache posture (PREREG_BUDGET_PARITY BP-H1b)"),
    "jcac_nobudget": SystemSpec(
        "jcac", params={"enforce_budget": False},
        description="PolyForge with the per-tenant budget filter LIFTED — what "
                    "the controller does when allowed to spend like the reactive "
                    "baselines (PREREG_BUDGET_PARITY BP-H2: tests whether WP1's "
                    "severity failure is constraint-induced)"),
    "jcac_evictcharged": SystemSpec(
        "jcac", params={"evict_overhead_us": 1104.9},
        description="PolyForge paying its own eviction cost: the cost-aware "
                    "policy's measured 1104.9 us p99 overhead "
                    "(eviction_comparison.csv, 0.0 for LRU/ARC) charged to AI "
                    "latency, which the published model gives away free "
                    "(PREREG_EVICTION_PARITY EP-H4)",
    ),
    # --- Wave 4 live-plane arms (PREREG_WAVE4_LIVE_PLANE.md §Arms) --------
    # The three-knob live plane's single-knob comparators. `replica-only` is
    # the strongest reactive replica baseline (HPA) with cache/tier held at a
    # fixed default — the prereg's named replica arm. `cache-only`/`tier-only`
    # are ablations of the joint controller with two knobs frozen, so the live
    # comparison isolates *jointness*. On the sim backend the freeze is applied
    # to each TenantConfig; live it is the Policy CRD min==max pin.
    "replica-only": SystemSpec(
        "hpa", lru_eviction=True,
        description="Wave 4 replica-only: reactive HPA replica control, cache "
                    "and tier fixed at the initial default (the prereg's "
                    "strongest single-knob reactive baseline)",
    ),
    "cache-only": SystemSpec(
        "jcac", knob_freeze=frozenset({"replicas", "tier"}),
        description="Wave 4 cache-only ablation: the joint MPC with replicas "
                    "and tier pinned, so only the cache knob actuates",
    ),
    "tier-only": SystemSpec(
        "jcac", knob_freeze=frozenset({"replicas", "cache"}),
        description="Wave 4 tier-only ablation: the joint MPC with replicas "
                    "and cache pinned, so only the tier knob actuates",
    ),
    # B1 follow-up (session 48). The live plane showed the published joint
    # controller losing WL-H1 to its own tier-only ablation on cost, for two
    # reasons that are the controller's, not the design's: it planned
    # open-loop on a plant model that predicted 3,500 ms where the cluster
    # served 1 ms, and its flat switching penalty exceeded the cost of the
    # replicas it could shed, so replicas ratcheted up and never came down.
    # This arm is jcac with both corrected: capacity calibrated from realized
    # CRUD headroom (plus a learned tier-latency offset), and a switching
    # penalty of half a replica-step. Same knobs, same objective, same
    # lattice; `jcac` itself is untouched.
    "jcac-calibrated": SystemSpec(
        "jcac", params={"headroom_calibration": True, "headroom_cap": 4.0,
                        "switch_penalty": 0.5 * 0.048 * 10 / 3600 / 0.01},
        description="Wave 4 follow-up: the joint MPC with headroom-calibrated "
                    "capacity, a learned tier-latency offset, and a half-replica "
                    "switching penalty",
    ),
    # B1' follow-up (session 48): the calibrated arm plus a replica dwell of
    # three control cycles (30 s) -- a tenant's replica move may not be
    # reversed within it. Damping for the oscillation CHURN_WAVE4_CALIBRATED.md
    # shows; unscored until a pre-registered sitting scores it.
    "jcac-calibrated-dwell": SystemSpec(
        "jcac", params={"headroom_calibration": True, "headroom_cap": 4.0,
                        "switch_penalty": 0.5 * 0.048 * 10 / 3600 / 0.01,
                        "replica_dwell_steps": 3},
        description="jcac-calibrated with a three-cycle replica dwell",
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
    "concurrency": SystemSpec(
        "concurrency", lru_eviction=True,
        description="Concurrency/queue-depth autoscaler (Knative-KPA / AIBrix-shaped, "
                    "PREREG_CONCURRENCY): in-flight-work signal, stable-window "
                    "scale-down; cache and tier fixed",
    ),
    # Learned joint controller (PREREG_LEARNED_CONTROL): the learned analog of
    # PolyForge's MPC and the joint-knob generalization of FIRM-replica.
    # `learned_trained` is the primary confirmatory arm — a shared policy
    # trained offline by baselines/train_learned.py and deployed frozen-greedy
    # from the committed Q-table (train=False, ε=0). `learned_online` is the
    # data-efficiency ablation: FIRM-style per-tenant online learning within
    # the run, which is data-starved over the joint space by design.
    "learned_trained": SystemSpec(
        "learned", lru_eviction=True, seeded=True,
        params={"train": False, "epsilon": 0.0, "shared": True,
                "qtable_path": str(REPO_ROOT / "research" / "results" / "learned_qtable.json")},
        description="Learned joint controller, offline-trained shared policy deployed "
                    "frozen-greedy (PREREG_LEARNED_CONTROL) — the learned analog of "
                    "PolyForge's MPC",
    ),
    "learned_online": SystemSpec(
        "learned", lru_eviction=True, seeded=True,
        params={"train": True, "shared": False, "epsilon": 0.1},
        description="Learned joint controller, FIRM-style per-tenant online learning "
                    "within the run (PREREG_LEARNED_CONTROL data-efficiency ablation)",
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
    # --- PREREG_LAYERED_FIX (WP3): the same ablation against a tier rule
    # that is not absorbing. The published arms above are untouched, so the
    # committed ablation table replays bit-for-bit (R4).
    "jcac_nojoint_v2": SystemSpec(
        "layered_v2",
        description="PolyForge minus joint controller, layer-local tier rule ranked "
                    "by measured latency instead of by tier name (PREREG_LAYERED_FIX)",
    ),
    "gptcache_v2": SystemSpec(
        "gptcache_v2", lru_eviction=True,
        description="GPTCache posture with the latency-ranked tier rule: cache "
                    "everything with LRU, HPA replicas, no absorbing tier",
    ),
    "jcac_noeviction": SystemSpec(
        "jcac", lru_eviction=True,
        description="PolyForge minus cost-aware eviction: LRU miss economics",
    ),
    "jcac_nofairness": SystemSpec(
        "jcac", gamma=0.0,
        description="PolyForge minus fairness objective (γ=0, no interference term)",
    ),
    # --- PREREG_MODEL_MISMATCH (WP6): the MPC with a wrong plant ----------
    # One belief wrong at a time, the world unchanged. `belief_scale` is
    # applied inside JCACController._project only, so the engine still scores
    # every arm against the published constants. `jcac` itself is the 1.0
    # control and is NOT duplicated here.
    "jcac_belief_cap5": SystemSpec(
        "jcac", params={"belief_scale": {"replica_capacity": 0.5}},
        description="PolyForge believing replica_capacity x0.5 (world unchanged)",
    ),
    "jcac_belief_cap75": SystemSpec(
        "jcac", params={"belief_scale": {"replica_capacity": 0.75}},
        description="PolyForge believing replica_capacity x0.75 (world unchanged)",
    ),
    "jcac_belief_cap125": SystemSpec(
        "jcac", params={"belief_scale": {"replica_capacity": 1.25}},
        description="PolyForge believing replica_capacity x1.25 (world unchanged)",
    ),
    "jcac_belief_cap20": SystemSpec(
        "jcac", params={"belief_scale": {"replica_capacity": 2.0}},
        description="PolyForge believing replica_capacity x2.0 (world unchanged)",
    ),
    "jcac_belief_tier5": SystemSpec(
        "jcac", params={"belief_scale": {"tier_cost": 0.5}},
        description="PolyForge believing tier_cost x0.5 (world unchanged)",
    ),
    "jcac_belief_tier75": SystemSpec(
        "jcac", params={"belief_scale": {"tier_cost": 0.75}},
        description="PolyForge believing tier_cost x0.75 (world unchanged)",
    ),
    "jcac_belief_tier125": SystemSpec(
        "jcac", params={"belief_scale": {"tier_cost": 1.25}},
        description="PolyForge believing tier_cost x1.25 (world unchanged)",
    ),
    "jcac_belief_tier20": SystemSpec(
        "jcac", params={"belief_scale": {"tier_cost": 2.0}},
        description="PolyForge believing tier_cost x2.0 (world unchanged)",
    ),
    "jcac_belief_cache5": SystemSpec(
        "jcac", params={"belief_scale": {"cache_half": 0.5}},
        description="PolyForge believing cache_half x0.5 (world unchanged)",
    ),
    "jcac_belief_cache75": SystemSpec(
        "jcac", params={"belief_scale": {"cache_half": 0.75}},
        description="PolyForge believing cache_half x0.75 (world unchanged)",
    ),
    "jcac_belief_cache125": SystemSpec(
        "jcac", params={"belief_scale": {"cache_half": 1.25}},
        description="PolyForge believing cache_half x1.25 (world unchanged)",
    ),
    "jcac_belief_cache20": SystemSpec(
        "jcac", params={"belief_scale": {"cache_half": 2.0}},
        description="PolyForge believing cache_half x2.0 (world unchanged)",
    ),
}
