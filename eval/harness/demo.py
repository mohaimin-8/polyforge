"""Side-by-side controller comparison on one scenario.

This is the presentation entry point (`scripts/demo_compare.py` is the
CLI): the same tenants, traffic, and cluster run under several systems
from the registry, and the result comes back structured so it can be
printed as a table, drawn as a knob trace, serialized as JSON for
automation, or asserted in tests. Nothing is pre-recorded: every arm runs
through `sim_backend.execute`, the code path of the pre-registered
campaigns, so a fair arm gets its sized cache and a tuned arm its tuned
parameters exactly as the records did.

The default cast is PREREG_FAIR_J's (audit 2026-09-26). The published
`hpa`/`static` comparison carried an LRU inference charge and a pinned
128 MB cache that the fair arms remove; `PUBLISHED_SYSTEMS` keeps that
original cast so the difference can be shown rather than hidden.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import sim_backend
from .config import RunSpec
from .systems import SYSTEMS
from .workloads import TENANT_MIXES
from .workloads import build as build_workload

#: PREREG_FAIR_J's cast: the reactive norm with a competently sized cache,
#: its event-driven sibling, the tuned layered stack holding the same three
#: knobs as separate rules, and PolyForge with the convergent solver.
DEFAULT_SYSTEMS = ("hpa_fair", "keda_fair", "jcac_nojoint_v2_tuned", "jcac_converged")
DEFAULT_BASELINE = "hpa_fair"

#: The pre-audit presentation cast, kept to show what the audit changed.
PUBLISHED_SYSTEMS = ("static", "hpa", "jcac")
PUBLISHED_BASELINE = "hpa"

#: The arm the takeaway speaks for: the first of these present.
POLYFORGE_ARMS = ("jcac_converged", "jcac")

#: Where the pre-registered comparison against the fair arms lives.
EVIDENCE_RECORD = "research/analysis/RESULTS_FAIR_J.md"

#: The five workload classes of the headline matrix (and of FAIR_J).
HEADLINE_WORKLOADS = ("crud_steady", "crud_bursty", "ai_cacheable", "ai_uncacheable",
                      "agentic")

#: Default scenario: agent chains + chat on a medium cluster, unchanged
#: since before the audit (it was not re-chosen after seeing fair results).
DEFAULT_SCENARIO = dict(workload="ai_uncacheable", tenant_mix="uniform",
                        cluster_size="medium", steps=120, seed=42)

LABELS = {
    "static": "Over-provision (peak)",
    "hpa": "HPA (as published)",
    "keda": "KEDA (as published)",
    "firm": "FIRM-replica (tuned)",
    "gptcache": "GPTCache+LRU (tuned)",
    "jcac": "PolyForge (published)",
    "hpa_fair": "HPA-fair (tuned)",
    "keda_fair": "KEDA-fair (tuned)",
    "hpa_fair_retuned": "HPA-fair (re-tuned)",
    "keda_fair_retuned": "KEDA-fair (re-tuned)",
    "jcac_nojoint_v2_tuned": "Layered 3-knob stack",
    "jcac_converged": "PolyForge (JCAC)",
}

#: (metric key, human label, format, higher_is_better)
METRIC_ROWS = (
    ("J", "J (scored; lower is better)", "{:.3f}", False),
    ("total_cost_usd", "cost for the window", "${:,.2f}", False),
    ("mean_violation", "mean SLO violation", "{:.3f}", False),
    ("violation_step_share", "tenant-steps violating", "{:.1%}", False),
    ("mean_jain", "Jain fairness index", "{:.3f}", True),
    ("cache_hit_rate", "AI cache hit rate", "{:.1%}", True),
    ("tier_none_step_share", "tenant-steps shedding AI", "{:.1%}", False),
)

#: Line styles cycle so arms holding identical settings stay visible.
LINE_STYLES = ("-", "--", "-.", ":")

#: Tier as a level, for drawing the tier knob on one axis.
TIER_LEVEL = {"none": 0, "small": 1, "mid": 2, "large": 3}

LABEL_COL = 30
VALUE_COL = 22


def composite_j(metrics: dict) -> float:
    """The records' J, constants included (research/analysis/stats.py,
    composite_objective): cost normalised per scored tenant-step of the
    8-tenant, 120-step design, plus 2 x violation plus 0.5 x (1 - Jain).
    It equals the records' J exactly at the default steps and an 8-tenant mix."""
    return (metrics["total_cost_usd"] / (119 * 8) / 0.01
            + 2.0 * metrics["mean_violation"] + 0.5 * (1.0 - metrics["mean_jain"]))


@dataclass(frozen=True)
class SystemOutcome:
    system: str
    label: str
    wall_s: float
    metrics: dict
    rows: tuple = ()  # per-tenant, per-step knob settings (collect_rows only)


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


def _run_system(name: str, scenario: dict, collect_rows: bool) -> SystemOutcome:
    run = RunSpec(
        experiment="demo", backend="sim", system=name,
        workload=scenario["workload"], tenant_mix=scenario["tenant_mix"],
        cluster_size=scenario["cluster_size"], rep=0, steps=scenario["steps"],
        run_id=f"demo-{name}", seed=scenario["seed"], store_timeseries=collect_rows,
    )
    out = sim_backend.execute(run)
    raw = out["metrics"]
    metrics = {key: raw[key] for key, *_ in METRIC_ROWS if key != "J"}
    metrics["J"] = composite_j(raw)
    return SystemOutcome(system=name, label=LABELS.get(name, name), wall_s=out["wall_s"],
                         metrics=metrics, rows=tuple(out["timeseries"] or ()))


def compare(systems: tuple[str, ...] = DEFAULT_SYSTEMS, baseline: str = DEFAULT_BASELINE,
            on_progress=None, collect_rows: bool = False, **scenario) -> Comparison:
    """Run `systems` on one scenario and return the structured comparison.

    `scenario` keys override DEFAULT_SCENARIO; unknown systems fail fast
    against the registry. `on_progress(outcome)` fires after each system
    so a CLI can narrate the runs as they land. `collect_rows` keeps the
    per-step knob settings that `plot_trace` draws.
    """
    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        raise ValueError(f"unknown systems {unknown} (known: {sorted(SYSTEMS)})")
    if baseline not in systems:
        raise ValueError(f"baseline {baseline!r} must be one of the compared systems")

    spec = {**DEFAULT_SCENARIO, **scenario}
    comparison = Comparison(scenario=spec, tenants=len(TENANT_MIXES[spec["tenant_mix"]]),
                            baseline=baseline)
    for name in systems:
        outcome = _run_system(name, spec, collect_rows)
        comparison.outcomes.append(outcome)
        if on_progress:
            on_progress(outcome)
    return comparison


def sweep(workloads: tuple[str, ...] = HEADLINE_WORKLOADS, **kw) -> list[Comparison]:
    """One comparison per workload class, everything else held fixed: the
    view that shows where each controller wins and where it does not."""
    return [compare(**{**kw, "workload": w}) for w in workloads]


def render_table(comparison: Comparison) -> str:
    """ASCII table (cp1252-safe) with deltas vs the baseline. '*' marks a
    change in the metric's better direction."""
    lines = []
    header = " " * LABEL_COL + "".join(f"{o.label:>{VALUE_COL}}" for o in comparison.outcomes)
    lines.append(header)
    lines.append("-" * len(header))
    for key, label, fmt, higher_better in METRIC_ROWS:
        cells = ""
        for o in comparison.outcomes:
            cell = fmt.format(o.metrics[key])
            delta = comparison.delta(o.system, key)
            if o.system != comparison.baseline and delta is not None:
                arrow = ("+" if delta >= 0 else "-") + f"{abs(delta):.0%}"
                good = (delta > 0) == higher_better if abs(delta) > 0.005 else None
                mark = " *" if good else ""
                cell = f"{cell} ({arrow}{mark})"
            cells += f"{cell:>{VALUE_COL}}"
        lines.append(f"{label:{LABEL_COL}s}{cells}")
    return "\n".join(lines)


def render_sweep(comparisons: list[Comparison]) -> str:
    """J per workload class and system; '<' marks the lowest J in each row."""
    first = comparisons[0]
    header = f"{'workload (J, lower is better)':{LABEL_COL}s}" + "".join(
        f"{o.label:>{VALUE_COL}}" for o in first.outcomes)
    lines = [header, "-" * len(header)]
    for c in comparisons:
        best = min(c.outcomes, key=lambda o: o.metrics["J"]).system
        cells = "".join(
            f"{o.metrics['J']:.3f}{' <' if o.system == best else '  '}".rjust(VALUE_COL)
            for o in c.outcomes)
        lines.append(f"{c.scenario['workload']:{LABEL_COL}s}{cells}")
    return "\n".join(lines)


def _treatment(comparison: Comparison) -> SystemOutcome | None:
    present = {o.system for o in comparison.outcomes}
    arm = next((a for a in POLYFORGE_ARMS if a in present), None)
    return comparison.outcome(arm) if arm else None


def _j_relation(treatment: SystemOutcome, other: SystemOutcome) -> str:
    base = other.metrics["J"]
    if abs(base) <= 1e-4:  # same near-zero rule as Comparison.delta
        diff = treatment.metrics["J"] - base
        return f"{abs(diff):.3f} {'lower' if diff < 0 else 'higher'} than {other.label} at {base:.3f}"
    rel = (treatment.metrics["J"] - base) / base
    if abs(rel) < 0.01:
        relation = "level with"
    elif rel < 0:
        relation = f"{abs(rel):.0%} lower than"
    else:
        relation = f"{rel:.0%} higher than"
    return f"{relation} {other.label} at {other.metrics['J']:.3f}"


def render_takeaway(comparison: Comparison) -> str:
    """The spoken sentence -- computed from the outcomes, never asserted,
    and never more than one scenario can carry."""
    t = _treatment(comparison)
    if t is None:
        return ""
    others = [o for o in comparison.outcomes if o.system != t.system]
    sentence = (f"On this one scenario PolyForge's J is {t.metrics['J']:.3f}: "
                + "; ".join(_j_relation(t, o) for o in others) + ".")
    shed = t.metrics["tier_none_step_share"]
    if shed > 0:
        none_else = all(o.metrics["tier_none_step_share"] == 0 for o in others)
        sentence += (f" It sheds AI traffic on {shed:.1%} of tenant-steps"
                     + (" and no other arm sheds any." if none_else else "."))
    return (sentence + " One scenario is an illustration, not evidence: the "
            f"pre-registered test against the fair arms is {EVIDENCE_RECORD}.")


def _per_step(rows: tuple, value) -> tuple[list[int], list[float]]:
    by_step: dict[int, float] = defaultdict(float)
    for row in rows:
        by_step[row["step"]] += value(row)
    steps = sorted(by_step)
    return steps, [by_step[s] for s in steps]


def _offered_demand(scenario: dict) -> tuple[list[int], list[float]]:
    _, buckets, _, _ = build_workload(scenario["workload"], scenario["tenant_mix"],
                                      scenario["cluster_size"], scenario["seed"],
                                      scenario["steps"])
    # The engine scores step s against bucket s (the *next* bucket).
    steps = list(range(1, scenario["steps"] + 1))
    return steps, [sum(sum(d.rps.values()) for d in buckets[s].values()) for s in steps]


def plot_trace(comparison: Comparison, path) -> Path:
    """Draw every arm's knob settings step by step: offered demand, then
    replicas, semantic cache and model tier summed or averaged over tenants,
    then cost per step. Needs compare(..., collect_rows=True)."""
    if any(not o.rows for o in comparison.outcomes):
        raise ValueError("plot_trace needs per-step rows: run compare(..., collect_rows=True)")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = comparison.tenants
    panels = (
        ("replicas (all tenants)", lambda r: r["replicas"]),
        ("semantic cache, MB (all tenants)", lambda r: r["cache_mb"]),
        ("model tier (tenant mean)", lambda r: TIER_LEVEL[r["tier"]] / n),
        ("cost per step, USD", lambda r: r["cost_usd"]),
    )
    fig, axes = plt.subplots(len(panels) + 1, 1, figsize=(10, 12), sharex=True)
    axes[0].plot(*_offered_demand(comparison.scenario), color="black")
    axes[0].set_ylabel("offered demand\nreq/s (all tenants)")
    for ax, (label, value) in zip(axes[1:], panels):
        for i, o in enumerate(comparison.outcomes):
            ax.plot(*_per_step(o.rows, value), label=o.label, linewidth=1.6,
                    linestyle=LINE_STYLES[i % len(LINE_STYLES)])
        ax.set_ylabel(label.replace(" (", "\n("))
    axes[3].set_yticks(list(TIER_LEVEL.values()), list(TIER_LEVEL))
    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=len(labels), fontsize=9,
               bbox_to_anchor=(0.5, 0.965))
    axes[-1].set_xlabel("control interval (10 s each)")
    spec = comparison.scenario
    fig.suptitle(f"{n} tenants | {spec['workload']} | '{spec['tenant_mix']}' mix | "
                 f"{spec['cluster_size']} cluster | seed {spec['seed']}")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = Path(path)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out
