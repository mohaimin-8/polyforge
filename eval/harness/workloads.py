"""Workload synthesis: taxonomy classes × tenant mixes × cluster sizes (W33).

The five workload classes are the W26 taxonomy (research/TAXONOMY.md) made
executable: each class is a per-tenant demand pattern over the model's six
request kinds, with the burstiness and mix that define the class. Tenant
mixes control who shares the cluster (SLO classes, budgets, scale
heterogeneity); cluster sizes control how much cluster there is to share —
that is what makes `cluster_size` a real experimental factor rather than a
label.

Everything is deterministic in the run seed. Repetition-to-repetition
variance comes from the simulator's Poisson jitter, not from this module:
two runs of the same cell see the same *underlying* demand curve and
different arrival noise, which is the variance a repetition is supposed to
measure.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from controller import ClusterLimits  # research/jcac_sim via harness sys.path
from model import Demand, TenantConfig


@dataclass(frozen=True)
class WorkloadClass:
    """Per-tenant demand template for one taxonomy class."""

    name: str
    base_rps: dict  # kind -> requests/second at scale 1.0
    shape: str  # steady | bursty | wave | sawtooth
    crud_base_ms: float


WORKLOAD_CLASSES = {
    "crud_bursty": WorkloadClass(
        name="crud_bursty",
        base_rps={"crud_read": 28.0, "crud_write": 8.0, "batch": 1.5},
        shape="bursty",
        crud_base_ms=40.0,
    ),
    "crud_steady": WorkloadClass(
        name="crud_steady",
        base_rps={"crud_read": 22.0, "crud_write": 6.0, "batch": 0.8},
        shape="steady",
        crud_base_ms=45.0,
    ),
    "ai_cacheable": WorkloadClass(
        name="ai_cacheable",
        base_rps={"chat": 4.0, "embed": 3.0, "crud_read": 5.0},
        shape="wave",
        crud_base_ms=60.0,
    ),
    "ai_uncacheable": WorkloadClass(
        name="ai_uncacheable",
        base_rps={"agent": 1.2, "chat": 2.0, "crud_read": 4.0},
        shape="sawtooth",
        crud_base_ms=60.0,
    ),
    "agentic": WorkloadClass(
        name="agentic",
        base_rps={"agent": 2.2, "embed": 0.5, "crud_read": 2.0},
        shape="bursty",
        crud_base_ms=70.0,
    ),
    # --- v3 overload classes (PREREG_V3.md §3) ---------------------------
    # The v1/v2 classes above never need more than ~2-3 replicas per tenant,
    # so the ±2-replicas-per-interval actuation clamp never binds and every
    # controller that reacts within one interval ties on SLO. These four
    # classes are sized (from the model constants, not from trial runs) so
    # the burst-onset replica climb exceeds what one interval of actuation
    # can deliver — the regime where proactive forecasting is structural,
    # not incremental. Amplitude derivations are frozen in PREREG_V3.md.
    "flash_crud": WorkloadClass(
        name="flash_crud",
        base_rps={"crud_read": 60.0, "crud_write": 10.0},
        shape="flash",
        crud_base_ms=40.0,
    ),
    "flash_ai": WorkloadClass(
        name="flash_ai",
        base_rps={"chat": 5.0, "embed": 3.0, "crud_read": 10.0},
        shape="flash",
        crud_base_ms=50.0,
    ),
    "spike_agentic": WorkloadClass(
        name="spike_agentic",
        base_rps={"agent": 1.5, "embed": 1.0, "crud_read": 5.0},
        shape="spike",
        crud_base_ms=60.0,
    ),
    # Control cell: identical demand envelope to flash_crud (0.5x-6.0x) but
    # reached at a slope actuation can follow (<0.4 replicas/step), so the
    # predicted outcome is a tie — the specificity check that any v3 win
    # comes from the actuation-binding regime and not from a generically
    # favorable world.
    "ramp_gentle": WorkloadClass(
        name="ramp_gentle",
        base_rps={"crud_read": 60.0, "crud_write": 10.0},
        shape="slowwave",
        crud_base_ms=40.0,
    ),
    # --- Wave 4 live-plane cells (PREREG_WAVE4_LIVE_PLANE.md §Cells) -----
    # Additive definitions for the two frozen cell names that had no class
    # yet; committed with the harness prep, before any live number exists.
    # `tier_mixed` is tier-load-bearing: agent traffic is the kind whose
    # latency the tier table orders mid < large < small (a small model
    # tool-loops), so tier choice moves both latency and $-cost materially.
    "tier_mixed": WorkloadClass(
        name="tier_mixed",
        base_rps={"chat": 3.0, "agent": 1.5, "crud_read": 5.0},
        shape="wave",
        crud_base_ms=60.0,
    ),
    # `joint_stress` is the all-three-knobs cell: bursty, cacheable (96-
    # prompt reuse pool in the live harness), tier-mixed demand that must be
    # co-scheduled — the cell the joint claim most needs.
    "joint_stress": WorkloadClass(
        name="joint_stress",
        base_rps={"chat": 5.0, "embed": 2.0, "agent": 1.0, "crud_read": 8.0},
        shape="bursty",
        crud_base_ms=50.0,
    ),
}


@dataclass(frozen=True)
class TenantSlot:
    slo_class: str
    budget_usd: float
    scale: float


# Eight tenants per cluster in every mix: the factor under study is the
# composition, not the population size.
TENANT_MIXES = {
    "uniform": [TenantSlot("standard", 5.0, 1.0)] * 8,
    "premium_heavy": (
        [TenantSlot("premium", 8.0, 1.0)] * 4
        + [TenantSlot("standard", 5.0, 1.0)] * 3
        + [TenantSlot("best-effort", 2.0, 1.0)]
    ),
    "besteffort_heavy": (
        [TenantSlot("premium", 8.0, 1.0)]
        + [TenantSlot("standard", 5.0, 1.0)] * 2
        + [TenantSlot("best-effort", 2.0, 1.0)] * 5
    ),
    # One whale, three regulars, four minnows: the fairness stressor.
    "whale": (
        [TenantSlot("standard", 12.0, 4.0)]
        + [TenantSlot("standard", 5.0, 1.0)] * 3
        + [TenantSlot("best-effort", 2.0, 0.6)] * 4
    ),
}


@dataclass(frozen=True)
class ClusterSize:
    name: str
    limits_cache_mb: int
    limits_replicas: int
    replica_max: int


CLUSTER_SIZES = {
    "small": ClusterSize("small", 2048, 24, 6),
    "medium": ClusterSize("medium", 4096, 48, 10),
    "large": ClusterSize("large", 8192, 96, 16),
}


def _shape_factor(shape: str, step: int, phase: float) -> float:
    """Demand multiplier at `step`, in (0, ~2.5]. Shapes are periodic so a
    120-step run sees several full cycles of its class's behavior."""
    if shape == "steady":
        return 1.0 + 0.1 * math.sin(2.0 * math.pi * (step / 40.0) + phase)
    if shape == "wave":
        return 1.0 + 0.25 * math.sin(2.0 * math.pi * (step / 60.0) + phase)
    if shape == "sawtooth":
        pos = ((step / 45.0) + phase / (2.0 * math.pi)) % 1.0
        return 0.6 + 0.8 * pos
    if shape == "bursty":
        pos = ((step / 24.0) + phase / (2.0 * math.pi)) % 1.0
        return 2.5 if pos < (1.0 / 3.0) else 0.25
    # v3 overload shapes (PREREG_V3.md §3). Periods sit inside the seasonal
    # detector's scan range (lags 8-48) so a period-aware forecaster *can*
    # lock on — whether that translates into an SLO win is what the v3
    # matrix measures.
    if shape == "flash":  # recurring flash crowd: 5-step 6x burst every 16
        pos = ((step / 16.0) + phase / (2.0 * math.pi)) % 1.0
        return 6.0 if pos < (5.0 / 16.0) else 0.5
    if shape == "spike":  # short hard spike: 2-step 8x burst every 12
        pos = ((step / 12.0) + phase / (2.0 * math.pi)) % 1.0
        return 8.0 if pos < (2.0 / 12.0) else 0.6
    if shape == "slowwave":  # same 0.5x-6.0x envelope as flash, gentle slope
        return 3.25 + 2.75 * math.sin(2.0 * math.pi * (step / 40.0) + phase)
    raise ValueError(f"unknown shape {shape!r}")


def build(workload: str, tenant_mix: str, cluster_size: str, seed: int, steps: int):
    """Materialize one run's world.

    Returns (tenant_ids, buckets, configs, limits): `steps + 1` demand
    buckets (the engine scores against the *next* bucket, so the last one
    is never scored), tenant configs from the mix, and cluster limits from
    the size. Deterministic in `seed`.
    """
    cls = WORKLOAD_CLASSES[workload]
    slots = TENANT_MIXES[tenant_mix]
    size = CLUSTER_SIZES[cluster_size]
    rng = random.Random(seed)

    tenant_ids = [f"t{i:02d}" for i in range(len(slots))]
    configs = {}
    phases = {}
    for tid, slot in zip(tenant_ids, slots):
        configs[tid] = TenantConfig(
            tenant_id=tid,
            slo_class=slot.slo_class,
            hourly_budget_usd=slot.budget_usd,
            replica_max=size.replica_max,
        )
        # Independent phase per tenant: bursts do not all land at once,
        # which is what makes multi-tenant packing non-trivial.
        phases[tid] = rng.uniform(0.0, 2.0 * math.pi)

    buckets = []
    for step in range(steps + 1):
        per_tenant = {}
        for tid, slot in zip(tenant_ids, slots):
            factor = _shape_factor(cls.shape, step, phases[tid]) * slot.scale
            rps = {kind: rate * factor for kind, rate in cls.base_rps.items()}
            per_tenant[tid] = Demand(rps=rps, crud_base_ms=cls.crud_base_ms)
        buckets.append(per_tenant)

    limits = ClusterLimits(cache_mb=size.limits_cache_mb, replicas=size.limits_replicas)
    return tenant_ids, buckets, configs, limits
