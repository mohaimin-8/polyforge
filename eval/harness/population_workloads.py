"""Population workloads for the sim backend (programme 2, WP8).

`popNNN` is workload NNN of `research/population/data/population.json`: eight
tenants built from real request-stream windows (BurstGPT v1.1, Azure LLM
2024), each a two-state Markov-modulated process moment-matched to its window
and rescaled to the tenant's drawn rate. Tenants are a construction: the
traces carry no tenant identity.

A population workload brings its own tenants, so its tenant mix is `own`
(demand from each tenant's MMPP2) or `native` (the same tenants, demand
replayed from real 10 s counts of the tenant's stream, `research/population/
native.py`: real days superposed at their own time of day and thinned to the
tenant's rate, no rescaling). Either way:
SLO classes come from the population file, and each budget is the tenant's
drawn factor times the active plant's cost of serving its mean demand on the
small tier (replicas sized for 70% utilisation, plus per-request spend). The
budget therefore follows the plant (mode A or B) instead of a fixed dollar
figure. The cluster size supplies the replica cap and cluster limits, as for
every other workload. All traffic is `chat`: the traces record LLM requests
and nothing else.

Deterministic in the run seed: the modulating chain of tenant t is seeded by
(the tenant's population seed, the run seed), so with shared seeds every arm
of a design point sees the same demand.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

PREFIX = "pop"
OWN_MIX = "own"
NATIVE_MIX = "native"
MIXES = (OWN_MIX, NATIVE_MIX)
REPO_ROOT = Path(__file__).resolve().parents[2]
POPULATION = REPO_ROOT / "research" / "population" / "data" / "population.json"
REFERENCE_UTILISATION = 0.7

_CACHE: dict = {}


def is_population(workload: str) -> bool:
    return workload.startswith(PREFIX) and workload[len(PREFIX):].isdigit()


def _load() -> dict:
    if "data" not in _CACHE:
        if not POPULATION.exists():
            raise FileNotFoundError(f"{POPULATION} missing: run research/population/population.py")
        data = json.loads(POPULATION.read_text(encoding="utf-8"))
        _CACHE["data"] = {w["workload"]: w for w in data["workloads"]}
    return _CACHE["data"]


def _module(name: str):
    key = f"population_{name}"
    if key not in _CACHE:
        path = REPO_ROOT / "research" / "population" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(mod)
        _CACHE[key] = mod
    return _CACHE[key]


def _generator():
    return _module("generator")


def _native_days() -> dict:
    if "days" not in _CACHE:
        _CACHE["days"] = _module("native").load_days()
    return _CACHE["days"]


def names(split: str | None = None) -> list[str]:
    return sorted(n for n, w in _load().items() if split is None or w["split"] == split)


def check(workload: str) -> None:
    if workload not in _load():
        raise ValueError(f"unknown population workload {workload!r}")


def reference_cost_usd_hr(rate_rps: float) -> float:
    """The active plant's hourly cost of serving `rate_rps` chat requests on
    the small tier with no cache: replicas for REFERENCE_UTILISATION, plus
    per-request spend."""
    import model

    wu = model.WORK_UNITS["chat"] * model.WU_AI_SCALE * model.WU_TIER_FACTOR["small"]
    replicas = max(1, math.ceil(rate_rps * wu / (REFERENCE_UTILISATION * model.REPLICA_CAPACITY_WU)))
    return replicas * model.REPLICA_COST_USD_HR + rate_rps * 3600.0 * model.TIER_COST_USD_PER_REQ["small"]


def build(workload: str, tenant_mix: str, size, seed: int, steps: int):
    """(tenant_ids, buckets, configs, limits) for one population workload;
    `size` is the harness's ClusterSize."""
    import numpy as np
    from controller import ClusterLimits
    from model import Demand, TenantConfig

    if tenant_mix not in MIXES:
        raise ValueError(f"population workload {workload} brings its own tenants: use a tenant mix in {MIXES}")
    check(workload)
    spec = _load()[workload]
    gen = _generator()
    tenant_ids = [t["tenant"] for t in spec["tenants"]]
    configs, series = {}, {}
    for t in spec["tenants"]:
        configs[t["tenant"]] = TenantConfig(
            tenant_id=t["tenant"], slo_class=t["slo_class"],
            hourly_budget_usd=t["budget_factor"] * reference_cost_usd_hr(t["rate_rps"]),
            replica_max=size.replica_max)
        rng = np.random.default_rng([t["seed"], seed])
        if tenant_mix == NATIVE_MIX:
            series[t["tenant"]] = _module("native").series(_native_days()[t["stream"]], t["rate_rps"],
                                                            steps + 1, rng)[0]
        else:
            series[t["tenant"]] = gen.MMPP2(**t["mmpp"]).rates(steps + 1, rng, scale=t["scale"])
    buckets = [{tid: Demand(rps={"chat": float(series[tid][step])}) for tid in tenant_ids}
               for step in range(steps + 1)]
    limits = ClusterLimits(cache_mb=size.limits_cache_mb, replicas=size.limits_replicas)
    return tenant_ids, buckets, configs, limits
