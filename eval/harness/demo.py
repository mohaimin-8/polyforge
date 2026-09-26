"""Side-by-side controller comparison on one live scenario.

This is the presentation entry point (`scripts/demo_compare.py` is the
CLI): the same tenants, traffic, and cluster run under several systems
from the registry, and the result comes back structured so it can be
printed as a table, serialized as JSON for automation, or asserted in
tests. Nothing is pre-recorded — every control step is solved on the
spot by the same code paths the full 1,800-run matrix used.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import simulate  # research/jcac_sim via harness sys.path

from . import workloads
from .systems import SYSTEMS, base_params, global_mix_transform, lru_miss_cost_factor

#: The default cast: the no-ops floor, the industry norm, and PolyForge.
DEFAULT_SYSTEMS = ("static", "hpa", "jcac")

#: Default scenario: agent chains + chat on a medium cluster — the cell
#: where every knob matters and the table reads unambiguously.
DEFAULT_SCENARIO = dict(workload="ai_uncacheable", tenant_mix="uniform",
                        cluster_size="medium", steps=120, seed=42)

LABELS = {
    "static": "Over-provision (peak)",
    "hpa": "Kubernetes HPA (tuned)",
    "keda": "KEDA (tuned)",
    "firm": "FIRM-replica (tuned)",
    "gptcache": "GPTCache+LRU (tuned)",
    "jcac": "PolyForge (JCAC)",
}

#: (metric attribute, human label, format, higher_is_better)
METRIC_ROWS = (
    ("total_cost_usd", "cost for the window", "${:,.2f}", False),
    ("mean_violation", "mean SLO violation", "{:.3f}", False),
    ("violation_step_share", "tenant-steps violating", "{:.1%}", False),
    ("mean_jain", "Jain fairness index", "{:.3f}", True),
    ("cache_hit_rate", "AI cache hit rate", "{:.1%}", True),
)


@dataclass(frozen=True)
class SystemOutcome:
    system: str
    label: str
    wall_s: float
    metrics: dict


@dataclass(frozen=True)
class Comparison:
    scenario: dict
    tenants: int
    baseline: str  # deltas are computed against this system
    outcomes: list[SystemOutcome] = field(default_factory=list)

    def outcome(self, system: str) -> SystemOutcome:
        for o in self.outcomes:
            if o.system == system:
                return o
        raise KeyError(system)

    def delta(self, system: str, metric: str) -> float | None:
        """Relative change vs the baseline; None when the baseline value is
        too close to zero for a percentage to mean anything."""
        base = self.outcome(self.baseline).metrics[metric]
        if abs(base) <= 1e-4:
            return None
        return (self.outcome(system).metrics[metric] - base) / abs(base)

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "tenants": self.tenants,
            "baseline": self.baseline,
            "systems": {
                o.system: {"label": o.label, "wall_s": round(o.wall_s, 2), **o.metrics}
                for o in self.outcomes
            },
        }


def _run_system(name: str, tenant_ids, buckets, configs, limits, seed: int,
                cluster_size: str) -> SystemOutcome:
    spec = SYSTEMS[name]
    params = base_params(spec, cluster_size)
    if spec.seeded:
        params["seed"] = seed
    started = time.time()
    result = simulate.run(
        spec.controller, tenant_ids, buckets, configs=configs, limits=limits,
        collect_rows=False, controller_params=params or None, jitter_seed=seed,
        plan_demand_transform=global_mix_transform if spec.blind_classifier else None,
        miss_cost_factor=lru_miss_cost_factor() if spec.lru_eviction else 1.0,
    )
    return SystemOutcome(
        system=name,
        label=LABELS.get(name, name),
        wall_s=time.time() - started,
        metrics={attr: getattr(result, attr) for attr, *_ in METRIC_ROWS},
    )


def compare(systems: tuple[str, ...] = DEFAULT_SYSTEMS, baseline: str = "hpa",
            on_progress=None, **scenario) -> Comparison:
    """Run `systems` on one scenario and return the structured comparison.

    `scenario` keys override DEFAULT_SCENARIO; unknown systems fail fast
    against the registry. `on_progress(outcome)` fires after each system
    so a CLI can narrate the runs as they land.
    """
    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        raise ValueError(f"unknown systems {unknown} (known: {sorted(SYSTEMS)})")
    if baseline not in systems:
        raise ValueError(f"baseline {baseline!r} must be one of the compared systems")

    spec = {**DEFAULT_SCENARIO, **scenario}
    tenant_ids, buckets, configs, limits = workloads.build(
        spec["workload"], spec["tenant_mix"], spec["cluster_size"],
        spec["seed"], spec["steps"],
    )

    comparison = Comparison(scenario=spec, tenants=len(tenant_ids), baseline=baseline)
    for name in systems:
        outcome = _run_system(name, tenant_ids, buckets, configs, limits, spec["seed"],
                              spec["cluster_size"])
        comparison.outcomes.append(outcome)
        if on_progress:
            on_progress(outcome)
    return comparison


def render_table(comparison: Comparison) -> str:
    """ASCII table (cp1252-safe) with deltas vs the baseline. '*' marks a
    change in the metric's better direction."""
    col = 26
    lines = []
    header = " " * 34 + "".join(f"{o.label:>{col}}" for o in comparison.outcomes)
    lines.append(header)
    lines.append("-" * len(header))
    for attr, label, fmt, higher_better in METRIC_ROWS:
        cells = ""
        for o in comparison.outcomes:
            cell = fmt.format(o.metrics[attr])
            delta = comparison.delta(o.system, attr)
            if o.system != comparison.baseline and delta is not None:
                arrow = ("+" if delta >= 0 else "-") + f"{abs(delta):.0%}"
                good = (delta > 0) == higher_better if abs(delta) > 0.005 else None
                mark = " *" if good else ""
                cell = f"{cell} ({arrow}{mark})"
            cells += f"{cell:>{col}}"
        lines.append(f"{label:34s}{cells}")
    return "\n".join(lines)


def render_takeaway(comparison: Comparison) -> str:
    """The spoken sentence — computed from the outcomes, never asserted."""
    base = comparison.outcome(comparison.baseline).metrics
    jc = comparison.outcome("jcac").metrics if any(
        o.system == "jcac" for o in comparison.outcomes) else None
    if jc is None:
        return ""
    saving = 1 - jc["total_cost_usd"] / base["total_cost_usd"]
    if jc["mean_violation"] <= base["mean_violation"]:
        slo = "while violating SLOs less"
    else:
        slo = (f"trading {jc['mean_violation'] - base['mean_violation']:.1%} points "
               "of SLO headroom for it")
    hit = ""
    if base["cache_hit_rate"] > 1e-4 and jc["cache_hit_rate"] > base["cache_hit_rate"]:
        hit = (f" and serving {jc['cache_hit_rate'] / base['cache_hit_rate']:,.1f}x "
               "more AI traffic from cache")
    others = [o for o in comparison.outcomes if o.system not in ("jcac", comparison.baseline)]
    others_clause = ""
    if others:
        worst = max(others, key=lambda o: o.metrics["total_cost_usd"])
        others_clause = f" and {worst.label} spends ${worst.metrics['total_cost_usd']:,.2f}"
    return (
        f'"Same tenants, same traffic, same cluster: '
        f'{comparison.outcome(comparison.baseline).label} spends '
        f'${base["total_cost_usd"]:,.2f}{others_clause}; PolyForge spends '
        f'${jc["total_cost_usd"]:,.2f} -- {saving:.0%} less than the norm -- {slo}{hit}."'
    )
