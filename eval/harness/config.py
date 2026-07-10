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
    # Per-step, per-tenant rows are large; keep them for the first
    # `timeseries_reps` repetitions only (figures need one trace, stats
    # need only the per-run aggregates).
    timeseries_reps: int = 1

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
    return spec


def run_identity(spec: ExperimentSpec, system: str, workload: str, mix: str,
                 cluster: str, rep: int) -> tuple[str, int]:
    material = "|".join(
        [SCHEMA_VERSION, spec.name, system, workload, mix, cluster, str(rep), str(spec.steps)]
    )
    if spec.transition_costs:
        # Appended only when set so every pre-existing run_id is unchanged.
        material += "|tc" 
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
                        ))
    return runs
