"""How far does the asymmetric convergence gate reach into the registered
campaigns? (programme 2, follow-up to WP22; read-only on programme 1.)

`JCACController._best_for_tenant` (the joint search) keeps its incumbent only
when the incumbent is a point of the anchored lattice; `_best_for_tenant_
decoupled` and `_best_for_tenant_knobwise` keep any feasible incumbent. After
a mid-sweep fallback to (replica floor, 0 MB, no tier) the per-knob arms can
therefore keep a state outside the lattice their moves are clamped to
(research/controls, `cap_binding_probe`).

This replays replication 0 of every registered per-knob arm (and, as a
control, the matching joint arm) with a probe that only observes: after each
plan it flags every tenant whose planned state is outside the anchored
lattice around its interval-start state without being a declared fallback.
Each replay is checked against the committed export (cost, violation, Jain,
bit for bit), so the counts describe the registered runs themselves.

    python gate_reach.py [--workers 2]      # writes data/gate_reach.csv and GATE_REACH.md
"""

from __future__ import annotations

import argparse
import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from controller import JCACController  # noqa: E402

# campaign file -> (per-knob arms, joint control arm)
CAMPAIGNS = {
    "matrix_jointness": (["jcac_decoupled", "jcac_knobwise"], "jcac_converged"),
    "matrix_level2": (["jcac_level_ratchet_decoupled"], "jcac_level_ratchet"),
    "matrix_level": (["jcac_level_decoupled"], "jcac_level"),
    "matrix_quality": (["jcac_quality_decoupled", "jcac_quality_knobwise"], None),
    "matrix_width_coupled": (["jcac_decoupled_wide"], None),
}
METRICS = ("total_cost_usd", "mean_violation", "mean_jain")


class GateProbe(JCACController):
    """Counts planned states outside the anchored lattice that are not fallbacks."""

    last: "GateProbe | None" = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decisions = 0
        self.outside = 0
        self.examples: list[tuple] = []
        GateProbe.last = self

    def plan(self, states, demands, interference=None):
        plans = super().plan(states, demands, interference)
        for tid, entry in plans.items():
            self.decisions += 1
            if self._plan_origin.get(tid) == "fallback":
                continue
            start = states[tid]
            if entry.state not in set(self._lattice(tid, start)):
                self.outside += 1
                if len(self.examples) < 3:
                    self.examples.append((self._cycle, tid, start, entry.state, self._plan_origin.get(tid)))
        return plans


def replay(job: tuple) -> dict:
    campaign, run = job
    import simulate
    from harness import sim_backend

    original = simulate.JCACController
    GateProbe.last = None
    simulate.JCACController = GateProbe
    try:
        result = sim_backend.execute(run)
    finally:
        simulate.JCACController = original
    probe = GateProbe.last
    GateProbe.last = None
    m = result["metrics"]
    return {"campaign": campaign, "system": run.system, "workload": run.workload, "tenant_mix": run.tenant_mix,
            "cluster_size": run.cluster_size, "run_id": run.run_id, **{k: m[k] for k in METRICS},
            "decisions": probe.decisions if probe else 0, "outside": probe.outside if probe else 0,
            "example": repr(probe.examples[0]) if probe and probe.examples else ""}


def jobs() -> list:
    from harness import config as hconfig

    out = []
    for campaign, (per_knob, joint) in CAMPAIGNS.items():
        spec = hconfig.load(REPO / "eval" / "experiments" / f"{campaign}.yaml")
        arms = set(per_knob) | ({joint} if joint else set())
        out += [(campaign, r) for r in hconfig.expand(spec) if r.system in arms and r.rep == 0]
    return out


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def integrity(rows: pd.DataFrame) -> pd.DataFrame:
    """Bit-identity of every replay with its campaign's committed export."""
    checks = []
    for campaign, g in rows.groupby("campaign"):
        # round_trip: pandas' default float parser is not correctly rounded and
        # can miss the exact value by one unit in the last place.
        export = pd.read_csv(REPO / "eval" / "results" / f"metrics_{campaign}.csv.gz", float_precision="round_trip")
        merged = g.merge(export[["run_id", *METRICS]], on="run_id", suffixes=("", "_export"))
        same = (merged[[f"{k}" for k in METRICS]].to_numpy() == merged[[f"{k}_export" for k in METRICS]].to_numpy())
        checks.append({"campaign": campaign, "replayed": len(g), "matched_in_export": len(merged),
                       "bit_identical": int(same.all(axis=1).sum())})
    return pd.DataFrame(checks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--report-only", action="store_true", help="rebuild the report from data/gate_reach.csv")
    args = ap.parse_args()
    if args.report_only:
        rows = pd.read_csv(HERE / "data" / "gate_reach.csv", float_precision="round_trip").fillna({"example": ""})
    else:
        todo = jobs()
        print(f"{len(todo)} replays on {args.workers} workers", flush=True)
        with Pool(args.workers) as pool:
            rows = pd.DataFrame(pool.map(replay, todo, chunksize=1))
        (HERE / "data").mkdir(exist_ok=True)
        rows.to_csv(HERE / "data" / "gate_reach.csv", index=False)
    checks = integrity(rows)
    summary = rows.groupby(["campaign", "system"]).agg(
        runs=("run_id", "size"), runs_with_outside=("outside", lambda s: int((s > 0).sum())),
        outside=("outside", "sum"), decisions=("decisions", "sum")).reset_index()
    L = ["# Reach of the asymmetric convergence gate into the registered campaigns", "",
         "Generated by `research/controls/gate_reach.py`. Replication 0 of every registered per-knob arm "
         "(and the matching joint arm as a control), replayed with a probe that only observes. "
         "`outside` = planned states outside the anchored lattice around the interval-start state that "
         "are not declared fallbacks.", "",
         "## Replays against the committed exports", "", md_table(checks), "",
         "## Plans outside the lattice", "", md_table(summary), ""]
    ex = rows[rows.example != ""].head(5)
    if len(ex):
        L += ["Examples (cycle, tenant, start state, planned state, origin):", ""]
        L += [f"- {r.campaign} / {r.system} / {r.workload} / {r.tenant_mix} / {r.cluster_size}: {r.example}"
              for r in ex.itertuples()]
        L.append("")
    (HERE / "GATE_REACH.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
