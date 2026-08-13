#!/usr/bin/env python3
"""WP13 — the multi-tenant extension of the cost separation.

`RESULTS_SEPARATION.md` derives the reactive-vs-predictive separation for a
**single tenant**, exactly, because tenants are independent there. The M3
close-out named the gap itself: the cluster cap couples them, and that
coupling is "load-bearing, not an optional refinement".

WP13 step 1 (`RESULTS_SEPARATION.md` §The coupling bracket) measured the
bracket the prose asserts and found only one arm of it real — the equal
share is genuinely infeasible, but there is no distinct "no-cap" model,
because the per-tenant replica ceiling never binds on these orbits. So this
extension cannot be specified as "narrow that bracket". What it does instead
is compute the coupled floor **exactly**.

Why exactly is possible. Nothing here is stochastic in the usual sense: each
tenant replays a deterministic periodic orbit with a phase drawn once,
uniformly, at run start (`workloads.build`). The joint phase space is a
finite product, every coincidence probability is exactly countable, and the
aggregate demand distribution is a convolution — not something that needs a
concentration inequality, which is what the M3 prose reached for.

================================================================
THE VALIDATION READING, STATED BEFORE IT WAS COMPUTED
================================================================

This is a derivation over already-committed campaign data, so no
pre-registration applies (mirroring how `bec0f62` itself landed). The
pre-stated reading is what keeps it honest, and it is committed before the
comparison is run:

  V1 (soundness, must hold). The coupled floor must **reduce exactly** to
      the uncoupled one when the cap cannot bind — one tenant, or a cap at
      or above the maximum aggregate need. A derivation that fails this is
      wrong, not merely weak.

  V2 (the claim). The derived coupled floor is a floor on *unavoidable*
      violation: no controller with this actuation authority can do better.
      So **every arm's measured `mean_violation` on the matching cell must
      be >= the derived floor**. If any arm measures strictly below it, the
      derivation is falsified and is reported as FAILED — not adjusted.

  V3 (does it bite?). The extension is only worth having if the coupled
      floor is materially above the uncoupled one. If the gap is ~0 the
      honest report is "the coupling is immaterial on these cells and the
      single-tenant derivation already describes them", which is a null
      result and is published as one.

No threshold is attached to V3 on purpose: it decides how the result is
*described*, never whether it is reported.

    python analysis_separation_mt.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis_separation as sep  # noqa: E402  (orbit constructors)
from stats import record_path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
import guarantee  # noqa: E402
from model import TenantConfig  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from harness import workloads  # noqa: E402

_RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
# flash_crud is the cell the orbit constructors mirror, and `small` is the
# cluster size whose equal share the M3 prose called infeasible.
CELL = {"workload": "flash_crud", "cluster_size": "small", "tenant_mix": "uniform"}
V3_DB = _RESULTS / "raw_sim_v3.duckdb"
V3_CSV = _RESULTS / "metrics_full.csv.gz"


def config_for(cluster_size: str) -> TenantConfig:
    size = workloads.CLUSTER_SIZES[cluster_size]
    return TenantConfig(tenant_id="t", slo_class="standard",
                        replica_max=size.replica_max)


def measured_violations() -> pd.DataFrame | None:
    """Per-arm mean_violation on the matching cell, from whichever committed
    campaign carries it. Returns None when no committed campaign has the
    cell, in which case V2 is reported as not evaluable rather than
    silently skipped."""
    cols = ("r.system, r.workload, r.tenant_mix, r.cluster_size, "
            "m.mean_violation")
    if V3_DB.exists():
        con = duckdb.connect(str(V3_DB), read_only=True)
        df = con.execute(f"select {cols} from runs r join metrics m "
                         "on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        con.close()
    elif V3_CSV.exists():
        df = pd.read_csv(V3_CSV)
    else:
        return None
    for key, value in CELL.items():
        if key not in df.columns:
            return None
        df = df[df[key] == value]
    return df if len(df) else None


def main() -> None:
    size = workloads.CLUSTER_SIZES[CELL["cluster_size"]]
    tenants = len(workloads.TENANT_MIXES[CELL["tenant_mix"]])
    cfg = config_for(CELL["cluster_size"])
    orbit = sep.flash_orbit()

    coupled = guarantee.coupled_floor(cfg, orbit, tenants, size.limits_replicas)
    # V1: the degenerate cases the derivation must reproduce.
    single = guarantee.coupled_floor(cfg, orbit, 1, size.limits_replicas)
    slack = guarantee.coupled_floor(cfg, orbit, tenants,
                                    max(guarantee.aggregate_need_distribution(
                                        coupled["needs"], tenants)))

    L: list[str] = []
    w = L.append
    w("# The coupled floor — multi-tenant extension of the cost separation (WP13)\n")
    w("Generated by `analysis_separation_mt.py` from "
      "`research/jcac_sim/guarantee.py`. Every number is computed at run "
      "time from the plant constants and the frozen demand construction; "
      "nothing is transcribed.\n")
    w("**This record does not replace `RESULTS_SEPARATION.md`.** That record "
      "is the single-tenant derivation and stands as committed; this one "
      "adds the coupling it explicitly scoped out.\n")

    w("\n## What the cap actually does\n")
    w(f"Cell: `{CELL['workload']}` / `{CELL['tenant_mix']}` / "
      f"`{CELL['cluster_size']}` — {tenants} tenants sharing "
      f"**{size.limits_replicas}** replicas, per-tenant ceiling "
      f"{size.replica_max}.\n")
    w(f"\nPer-orbit-position replica need for one tenant: "
      f"`{list(coupled['needs'])}` — {coupled['needs'].count(max(coupled['needs']))} "
      f"of {len(coupled['needs'])} positions need the peak of "
      f"{max(coupled['needs'])}.\n")
    dist = guarantee.aggregate_need_distribution(coupled["needs"], tenants)
    expected = sum(total * p for total, p in dist.items())
    w(f"Aggregate need is a sum of {tenants} independent draws, so its "
      "distribution is an **exact convolution** — countable, not bounded:\n")
    w("\n| aggregate replicas needed | probability |")
    w("|---:|---:|")
    for total in sorted(dist):
        mark = " **(over cap)**" if total > size.limits_replicas else ""
        w(f"| {total}{mark} | {dist[total]:.6f} |")
    w(f"\nE[aggregate need] = **{expected:.3f}** against a cap of "
      f"{size.limits_replicas}, so on average there is headroom — and the "
      f"cap still binds with probability "
      f"**{coupled['bind_probability']:.4f}**. That tail is the coupling, and "
      "it is why an expectation argument alone would have missed it.\n")

    w("\n## The coupled floor\n")
    w("Allocation under contention follows the simulator's own rule: "
      "`controller._sweep_order` is `sorted(configs)` and the sweep is "
      "first-come-first-served on the shared caps, so an earlier tenant "
      "claims contended capacity before a later one sees it. A floor that "
      "allocated differently would describe a different system.\n")
    w("\n| quantity | value |")
    w("|---|---:|")
    w(f"| uncoupled floor (each tenant alone) | **{coupled['uncoupled_violation']:.6f}** |")
    w(f"| coupled floor (shared cap) | **{coupled['coupled_violation']:.6f}** |")
    w(f"| gap the single-tenant derivation cannot see | **{coupled['gap']:.6f}** |")

    # --- V1 -----------------------------------------------------------------
    v1_single = abs(single["coupled_violation"] - single["uncoupled_violation"]) < 1e-12
    v1_slack = abs(slack["coupled_violation"] - slack["uncoupled_violation"]) < 1e-12
    w("\n### V1 — soundness (pre-stated: must hold)\n")
    w("\n| degenerate case | coupled | uncoupled | identical? |")
    w("|---|---:|---:|---|")
    w(f"| one tenant | {single['coupled_violation']:.6f} | "
      f"{single['uncoupled_violation']:.6f} | {'yes' if v1_single else '**NO**'} |")
    w(f"| cap at the maximum aggregate need | {slack['coupled_violation']:.6f} | "
      f"{slack['uncoupled_violation']:.6f} | {'yes' if v1_slack else '**NO**'} |")
    w(f"\n**V1 {'PASS' if v1_single and v1_slack else 'FAIL'}** — the coupled "
      "derivation "
      + ("reduces exactly to the uncoupled one wherever the cap cannot bind.\n"
         if v1_single and v1_slack else
         "does **not** reduce to the uncoupled one, so it is wrong and "
         "nothing below should be read.\n"))

    # --- V2 -----------------------------------------------------------------
    w("\n### V2 — the claim (pre-stated: every arm must measure at or above the floor)\n")
    measured = measured_violations()
    if measured is None:
        w("**Not evaluable.** No committed campaign carries this cell with "
          "the columns needed, so V2 is reported as unevaluated rather than "
          "quietly skipped. The derivation stands on V1 and V3 until a "
          "campaign that includes it lands.\n")
        v2 = None
    else:
        floor = coupled["coupled_violation"]
        per_arm = measured.groupby("system").mean_violation.mean().sort_values()
        below = per_arm[per_arm < floor - 1e-9]
        w("\n| arm | measured mean_violation | at or above the floor? |")
        w("|---|---:|---|")
        for arm, value in per_arm.items():
            w(f"| `{arm}` | {value:.6f} | "
              f"{'yes' if value >= floor - 1e-9 else '**NO**'} |")
        v2 = len(below) == 0
        w(f"\n**V2 {'PASS' if v2 else 'FAIL'}** — "
          + (f"every arm measures at or above the derived floor of "
             f"{floor:.6f}.\n" if v2 else
             f"{', '.join(below.index)} measured strictly below the derived "
             f"floor of {floor:.6f}. Per the pre-stated reading the "
             "derivation is FALSIFIED and is reported as such, not "
             "adjusted.\n"))

    # --- V3 -----------------------------------------------------------------
    w("\n### V3 — does it bite? (pre-stated: describes, never gates)\n")
    if coupled["gap"] > 0:
        w(f"The coupled floor is **{coupled['gap']:.6f}** above the uncoupled "
          "one. The single-tenant derivation reports a floor of "
          f"{coupled['uncoupled_violation']:.6f} for this cell — i.e. it says "
          "a perfect controller could hold the SLO exactly. The coupled "
          "derivation says it cannot: that much violation is **capacity, not "
          "control quality**, and no tuning removes it.\n")
        w("That is the practical payoff. A measured violation at or below "
          f"{coupled['coupled_violation']:.6f} on this cell is not evidence "
          "of a control deficiency, and any comparison that reads it as one "
          "is comparing controllers to an unreachable ideal.\n")
    else:
        w("The gap is zero: the coupling is immaterial on this cell and the "
          "single-tenant derivation already describes it. Reported as the "
          "null result it is.\n")

    w("\n## Scope — what this record does NOT claim\n")
    w("- It is a floor on **violation**, not a cost separation. The cost "
      "side of `RESULTS_SEPARATION.md` is untouched and remains "
      "single-tenant.")
    w("- One cell. The construction generalises — the enumeration is over "
      "distinct need levels, not orbit positions, so it stays small — but "
      "only this cell is computed here.")
    w("- The allocation rule is the simulator's FCFS sweep. A different "
      "contention rule gives a different floor, and a real cluster's "
      "scheduler is not this one.")
    w("- The retracted sign-agreement claim from M3 stays retracted; nothing "
      "here revives it.\n")

    out = record_path("RESULTS_SEPARATION_MT.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"V1={'PASS' if v1_single and v1_slack else 'FAIL'} "
          f"V2={'n/a' if v2 is None else ('PASS' if v2 else 'FAIL')} "
          f"gap={coupled['gap']:.6f}")


if __name__ == "__main__":
    main()
