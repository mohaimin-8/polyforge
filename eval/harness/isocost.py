"""Per-run resolution of the iso-cost marker params (PREREG_V2 §5).

`static_isocost` and `cache_isocost` carry `{"isocost": "static"|"cache"}`
in their SystemSpec; the sim backend calls `resolve` to replace that marker
with the concrete controller parameter for the run's matrix cell, looked up
from `eval/baselines/isocost_budgets.yaml` — the table that
`eval/baselines/isocost.py` derives from the completed matrix_v2 database.
Running an iso-cost system before the table exists fails loudly: budgets
come from measured spend, never from defaults.
"""

from __future__ import annotations

from functools import lru_cache

from . import EVAL_DIR

BUDGETS_PATH = EVAL_DIR / "baselines" / "isocost_budgets.yaml"


@lru_cache(maxsize=1)
def _table() -> dict:
    if not BUDGETS_PATH.exists():
        raise FileNotFoundError(
            f"{BUDGETS_PATH} not found — run `python baselines/isocost.py` "
            "after matrix_v2 completes (PREREG_V2 §5)."
        )
    import yaml

    return yaml.safe_load(BUDGETS_PATH.read_text(encoding="utf-8"))


def cell_key(workload: str, tenant_mix: str, cluster_size: str) -> str:
    return f"{workload}|{tenant_mix}|{cluster_size}"


def resolve(params: dict, run) -> dict:
    kind = params.pop("isocost")
    cell = _table()["cells"][cell_key(run.workload, run.tenant_mix, run.cluster_size)]
    if kind == "static":
        params["fixed_replicas"] = int(cell["static_replicas_per_tenant"])
    elif kind == "cache":
        params["fixed_cache_mb"] = int(cell["cache_level_mb"])
    else:
        raise ValueError(f"unknown isocost kind {kind!r}")
    return params
