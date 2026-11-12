"""The WP9 tuning protocol as pure functions; `tune.py` executes it.

Scoring objective (the composite every programme-1 and programme-2 pilot
scores with; `objective_j` in research/sensitivity/morris_pilot.py):

    J = cost / (scored steps x tenants) / 0.01 + 2 x mean violation
        + 0.5 x (1 - mean Jain) [+ q x mean quality loss]

The quality term is added only when the tuning run sets q > 0. The cost
term divides by the run's own scored tenant-steps. A population workload
builds steps + 1 demand buckets, so it scores all `steps` steps: at 120 steps
that is 120 x 8, where morris_pilot's objective_j hard-codes 119 x 8 (the
count of the programme-1 workloads, which build `steps` buckets); its cost
term is therefore 120/119 of this one on population runs.

Workload class rule, fixed before any tuning result (2026-11-12): a
population workload's class is the stream family carrying the larger share of
its demand, the sum of its eight tenants' drawn `rate_rps`. Families: the
traces' conversation streams (azure2024/conv, burstgpt/ChatGPT/conv,
burstgpt/GPT-4/conv) are `conversation`; the API and code streams
(burstgpt/ChatGPT/api, burstgpt/GPT-4/api, azure2024/code) are `api_or_code`.
A tie goes to the alphabetically first family. It reads
research/population/data/population.json only, so it is deterministic.
Counts it gives: tuning half 64 api_or_code / 56 conversation, evaluation
half 56 / 64. (Why demand share and not a tenant count: 29 of 120 tuning
workloads tie 4-4 on tenant counts; none tie on demand share.)

Selection: per arm, the configuration with the lowest mean J over the tuning
workloads in scope (all of them, or one class), among the configurations that
completed EVERY tuning run with a finite J (a failure disqualifies a
configuration in every scope, so a class never inherits a configuration that
crashed elsewhere); ties go to the lower index. Evaluation rows never enter
selection (`select` refuses them).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
POPULATION = REPO / "research" / "population" / "data" / "population.json"

COST_SCALE_USD = 0.01
VIOLATION_WEIGHT = 2.0
FAIRNESS_WEIGHT = 0.5

CLASSES = ("api_or_code", "conversation")
STREAM_FAMILY = {
    "azure2024/conv": "conversation",
    "burstgpt/ChatGPT/conv": "conversation",
    "burstgpt/GPT-4/conv": "conversation",
    "azure2024/code": "api_or_code",
    "burstgpt/ChatGPT/api": "api_or_code",
    "burstgpt/GPT-4/api": "api_or_code",
}
CLASS_RULE = ("the stream family carrying the larger share of the workload's demand (sum of its tenants' "
              "rate_rps); conversation = azure2024/conv, burstgpt/ChatGPT/conv, burstgpt/GPT-4/conv; "
              "api_or_code = burstgpt/ChatGPT/api, burstgpt/GPT-4/api, azure2024/code; ties to the "
              "alphabetically first family")
GRANULARITIES = ("overall", "per_class")


def objective(metrics: dict, tenants: int, quality_weight: float = 0.0) -> dict:
    """J and its terms for one run's metrics (sim_backend.execute's dict)."""
    steps = metrics["steps"]
    if steps <= 0 or tenants <= 0:
        raise ValueError("a run with no scored steps or tenants has no J")
    terms = {
        "cost_term": metrics["total_cost_usd"] / (steps * tenants) / COST_SCALE_USD,
        "violation_term": VIOLATION_WEIGHT * metrics["mean_violation"],
        "unfairness_term": FAIRNESS_WEIGHT * (1.0 - metrics["mean_jain"]),
        "quality_term": quality_weight * metrics["mean_quality_loss"],
    }
    return {"J": sum(terms.values()), **terms}


# --------------------------------------------------------------------------
# Workload classes
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _population() -> dict:
    data = json.loads(POPULATION.read_text(encoding="utf-8"))
    return {w["workload"]: w for w in data["workloads"]}


def class_of_shares(shares: dict) -> str:
    """The family with the largest share; ties to the alphabetically first."""
    best = max(shares.values())
    return sorted(c for c, v in shares.items() if v == best)[0]


def workload_class(workload: str) -> str:
    spec = _population().get(workload)
    if spec is None:
        raise ValueError(f"unknown population workload {workload!r}")
    shares = {c: 0.0 for c in CLASSES}
    for tenant in spec["tenants"]:
        family = STREAM_FAMILY.get(tenant["stream"])
        if family is None:
            raise ValueError(f"{workload}: stream {tenant['stream']!r} has no family in the class rule")
        shares[family] += float(tenant["rate_rps"])
    return class_of_shares(shares)


def split_names(split: str) -> list[str]:
    return sorted(n for n, w in _population().items() if w["split"] == split)


def pick_workloads(split: str, n: int | None = None) -> list[str]:
    """The workloads of one half, all of them, or the first `n` in name
    order stratified by class (class i of k gets n // k, plus one while
    i < n % k), so a short run still sees every class."""
    names = split_names(split)
    if n is None or n >= len(names):
        return names
    if n < 1:
        raise ValueError("need at least one workload")
    by_class = {c: [w for w in names if workload_class(w) == c] for c in CLASSES}
    out = []
    for i, c in enumerate(CLASSES):
        out += by_class[c][: n // len(CLASSES) + (1 if i < n % len(CLASSES) else 0)]
    return sorted(out)


# --------------------------------------------------------------------------
# Selection and the evaluation plan
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Pick:
    config: int
    mean_J: float
    n: int


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def best_config(scores: dict[int, dict[str, float]], workloads: list[str]) -> Pick | None:
    """Lowest mean J over `workloads` among the configurations in `scores`
    with a finite J on every one of them; ties to the lower index. None if
    none qualifies."""
    best = None
    for config in sorted(scores):
        values = [scores[config].get(w) for w in workloads]
        if not workloads or not all(_finite(v) for v in values):
            continue
        mean = float(np.mean(values))
        if best is None or mean < best.mean_J:
            best = Pick(config, mean, len(workloads))
    return best


def completed(scores: dict[int, dict[str, float]], workloads: list[str]) -> dict[int, dict[str, float]]:
    """The configurations with a finite J on every tuning workload."""
    return {c: s for c, s in scores.items() if all(_finite(s.get(w)) for w in workloads)}


def select(rows: list[dict], classes: dict[str, str]) -> dict[str, dict]:
    """{arm: {"overall": Pick, "per_class": {class: Pick}}} from tuning rows
    (keys phase, arm, config, workload, J). `classes` maps every tuning
    workload to its class."""
    scores: dict[str, dict[int, dict[str, float]]] = {}
    for r in rows:
        if r["phase"] != "tuning":
            raise ValueError(f"selection reads tuning rows only, got a {r['phase']!r} row")
        if r["workload"] not in classes:
            raise ValueError(f"tuning workload {r['workload']!r} has no class")
        scores.setdefault(r["arm"], {}).setdefault(int(r["config"]), {})[r["workload"]] = r["J"]
    tuning = sorted(classes)
    out = {}
    for name, all_configs in scores.items():
        by_config = completed(all_configs, tuning)
        per_class = {}
        for c in sorted(set(classes.values())):
            pick = best_config(by_config, [w for w in tuning if classes[w] == c])
            if pick is not None:
                per_class[c] = pick
        out[name] = {"overall": best_config(by_config, tuning), "per_class": per_class}
    return out


def evaluation_plan(selection: dict[str, dict], workloads: list[str], classes: dict[str, str],
                    granularities: tuple = GRANULARITIES) -> list[dict]:
    """One job per distinct (arm, config, evaluation workload), tagged with
    the granularities it serves. Per class: the class's configuration, or the
    overall one (`per_class_fallback`) when the class had no tuning workload."""
    jobs: dict[tuple, dict] = {}
    for name, picks in selection.items():
        if picks["overall"] is None:
            continue
        for w in workloads:
            chosen = {}
            if "overall" in granularities:
                chosen["overall"] = (picks["overall"].config, False)
            if "per_class" in granularities:
                pick = picks["per_class"].get(classes[w])
                chosen["per_class"] = (pick.config, False) if pick else (picks["overall"].config, True)
            for granularity, (config, fallback) in chosen.items():
                job = jobs.setdefault((name, config, w), {"arm": name, "config": config, "workload": w,
                                                          "granularities": [], "per_class_fallback": False})
                job["granularities"].append(granularity)
                job["per_class_fallback"] = job["per_class_fallback"] or fallback
    return list(jobs.values())


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------

def bootstrap_ci(values, seed: int = 20261112, draws: int = 5000) -> tuple[float, float]:
    """95% percentile interval of the mean over resampled workloads."""
    v = np.asarray(values, dtype=float)
    if v.size < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, v.size, size=(draws, v.size))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluation_table(rows: list[dict], granularity: str) -> dict[str, dict[str, dict]]:
    """{arm: {workload: row}} of the evaluation rows serving `granularity`."""
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r["phase"] == "evaluation" and granularity in r["granularities"]:
            out.setdefault(r["arm"], {})[r["workload"]] = r
    return out
