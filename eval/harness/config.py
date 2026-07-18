"""Experiment specification: YAML in, run matrix out (W33).

A run's identity is a hash of everything that determines its outcome —
experiment name, system, workload, tenant mix, cluster size, repetition,
step count, and the schema version. Identical inputs always produce the
same run_id, which is what makes resume ("skip runs already valid in the
DB") and spot-check replay ("re-execute this run_id and compare") sound.
The per-run RNG seed is derived from the run_id, so a replayed run sees
exactly the same jittered demand.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import SCHEMA_VERSION
from .systems import SYSTEMS
from .workloads import CLUSTER_SIZES, TENANT_MIXES, WORKLOAD_CLASSES

BACKENDS = ("sim", "cluster")

ECONOMY_KEYS = {
    "tier_cost_small", "tier_cost_mid", "tier_cost_large",
    "cache_hit_max", "cache_half_mb",
}

# Structural model-form overrides (Wave 5 preregs: PREREG_LM_ADOPTION,
# PREREG_MIXTURE_P95, PREREG_TIER_WU). Same contract as economy: sim-only,
# applies to the world and every controller's beliefs identically, and an
# empty mapping is the published form leaving every run_id unchanged.
MODEL_FORM_KEYS = {
    "congestion_exponent",       # a in g(rho) = (1-rho)^(-a)
    "p95_tail_f0", "p95_tail_b",  # p95/mean = f0*(1-min(rho,sat))^(-b), paired
    "mixture_p95",               # 1: ai_p95 is the true hit/miss mixture percentile
    "wu_tier_mid", "wu_tier_large",  # per-AI-request capacity multiplier vs small
}


@dataclass(frozen=True)
class RunSpec:
    """One cell of the experiment matrix. Frozen: a run's definition never
    mutates after expansion."""

    experiment: str
    backend: str
    system: str
    workload: str
    tenant_mix: str
    cluster_size: str
    rep: int
    steps: int
    run_id: str
    seed: int
    store_timeseries: bool
    transition_costs: bool = False
    interference: bool = False
    # Economy override (PREREG_TIER_RATIO / PREREG_HK_ADOPTION): sorted
    # (key, value) pairs, empty for the published economy. Kept as a tuple
    # so the frozen spec stays hashable and deterministic.
    economy: tuple = ()
    # Structural model-form override (Wave 5 preregs), same tuple contract.
    model_form: tuple = ()


@dataclass
class ExperimentSpec:
    name: str
    backend: str = "sim"
    steps: int = 120
    reps: int = 5
    systems: list = field(default_factory=lambda: ["jcac"])
    workloads: list = field(default_factory=lambda: list(WORKLOAD_CLASSES))
    tenant_mixes: list = field(default_factory=lambda: list(TENANT_MIXES))
    cluster_sizes: list = field(default_factory=lambda: list(CLUSTER_SIZES))
    output: str = "eval/results/results.duckdb"
    retries: int = 2
    # Reconfiguration realism (replica startup lag, cache warm-up) in the
    # scoring engine; see simulate.run(transition_costs=...).
    transition_costs: bool = False
    # Noisy-neighbor interference injection (v2 Phase 4); see
    # simulate.run(interference_injection=...).
    interference: bool = False
    # Per-step, per-tenant rows are large; keep them for the first
    # `timeseries_reps` repetitions only (figures need one trace, stats
    # need only the per-run aggregates).
    timeseries_reps: int = 1
    # Economy override, sim backend only. Known keys: tier_cost_small,
    # tier_cost_mid, tier_cost_large (USD per request), cache_hit_max,
    # cache_half_mb. An empty mapping is the published economy and leaves
    # every pre-existing run_id unchanged.
    economy: dict = field(default_factory=dict)
    # Structural model-form override, sim backend only. Known keys:
    # MODEL_FORM_KEYS above. Empty mapping = the published forms.
    model_form: dict = field(default_factory=dict)

    def total_runs(self) -> int:
        return (
            len(self.systems)
            * len(self.workloads)
            * len(self.tenant_mixes)
            * len(self.cluster_sizes)
            * self.reps
        )


def _check_membership(values: list, universe, kind: str) -> None:
    unknown = [v for v in values if v not in universe]
    if unknown:
        raise ValueError(f"unknown {kind}: {unknown} (known: {sorted(universe)})")


def load(path: str | Path) -> ExperimentSpec:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "name" not in raw:
        raise ValueError(f"{path}: experiment YAML must be a mapping with a 'name'")
    known = {f.name for f in ExperimentSpec.__dataclass_fields__.values()}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    spec = ExperimentSpec(**raw)

    if spec.backend not in BACKENDS:
        raise ValueError(f"unknown backend {spec.backend!r} (known: {BACKENDS})")
    _check_membership(spec.systems, SYSTEMS, "system")
    _check_membership(spec.workloads, WORKLOAD_CLASSES, "workload class")
    _check_membership(spec.tenant_mixes, TENANT_MIXES, "tenant mix")
    _check_membership(spec.cluster_sizes, CLUSTER_SIZES, "cluster size")
    if spec.reps < 1 or spec.steps < 3:
        raise ValueError("reps must be >= 1 and steps >= 3")
    if spec.economy:
        if spec.backend != "sim":
            raise ValueError("economy overrides are sim-only; the live data "
                             "plane's economy is physical, not configurable")
        unknown = set(spec.economy) - ECONOMY_KEYS
        if unknown:
            raise ValueError(f"unknown economy keys {sorted(unknown)} "
                             f"(known: {sorted(ECONOMY_KEYS)})")
        bad = {k: v for k, v in spec.economy.items()
               if not isinstance(v, (int, float)) or v < 0}
        if bad:
            raise ValueError(f"economy values must be non-negative numbers: {bad}")
    if spec.model_form:
        if spec.backend != "sim":
            raise ValueError("model_form overrides are sim-only; the live data "
                             "plane's physics are physical, not configurable")
        unknown = set(spec.model_form) - MODEL_FORM_KEYS
        if unknown:
            raise ValueError(f"unknown model_form keys {sorted(unknown)} "
                             f"(known: {sorted(MODEL_FORM_KEYS)})")
        bad = {k: v for k, v in spec.model_form.items()
               if not isinstance(v, (int, float)) or v < 0}
        if bad:
            raise ValueError(f"model_form values must be non-negative numbers: {bad}")
        if ("p95_tail_f0" in spec.model_form) != ("p95_tail_b" in spec.model_form):
            raise ValueError("p95_tail_f0 and p95_tail_b must be set together")
    return spec


def run_identity(spec: ExperimentSpec, system: str, workload: str, mix: str,
                 cluster: str, rep: int) -> tuple[str, int]:
    material = "|".join(
        [SCHEMA_VERSION, spec.name, system, workload, mix, cluster, str(rep), str(spec.steps)]
    )
    if spec.transition_costs:
        # Appended only when set so every pre-existing run_id is unchanged.
        material += "|tc"
    if spec.interference:
        material += "|if"
    if spec.economy:
        material += "|econ:" + ",".join(
            f"{k}={spec.economy[k]:g}" for k in sorted(spec.economy)
        )
    if spec.model_form:
        material += "|form:" + ",".join(
            f"{k}={spec.model_form[k]:g}" for k in sorted(spec.model_form)
        )
    digest = hashlib.sha256(material.encode()).hexdigest()
    run_id = digest[:16]
    seed = int(digest[16:28], 16) % (2**31 - 1)
    return run_id, seed


def expand(spec: ExperimentSpec) -> list[RunSpec]:
    """The full matrix in deterministic order. Order matters: the runner
    reports progress against it and resume must be stable."""
    runs = []
    for system in spec.systems:
        for workload in spec.workloads:
            for mix in spec.tenant_mixes:
                for cluster in spec.cluster_sizes:
                    for rep in range(spec.reps):
                        run_id, seed = run_identity(spec, system, workload, mix, cluster, rep)
                        runs.append(RunSpec(
                            experiment=spec.name,
                            backend=spec.backend,
                            system=system,
                            workload=workload,
                            tenant_mix=mix,
                            cluster_size=cluster,
                            rep=rep,
                            steps=spec.steps,
                            run_id=run_id,
                            seed=seed,
                            store_timeseries=rep < spec.timeseries_reps,
                            transition_costs=spec.transition_costs,
                            interference=spec.interference,
                            economy=tuple(sorted(spec.economy.items())),
                            model_form=tuple(sorted(spec.model_form.items())),
                        ))
    return runs
