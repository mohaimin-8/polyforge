#!/usr/bin/env python3
"""WP13 step 2 — the coupled floor, over the allocation the planner performs.

`RESULTS_SEPARATION_MT.md` (step 1) derived a coupled floor and V2 FALSIFIED
it: `keda` measured 0.001886 against a derived floor of 0.036487. That record
stands as committed. The diagnosis it recorded is the starting point here, and
it was a defect in the ALLOCATION RULE, not in the demand model -- the
aggregate convolution was checked against `workloads.build` and matches.

`fcfs_allocation` hands each tenant `min(need, remaining)` FROM ZERO, so a late
tenant under contention is allocated nothing and scored at one replica. The
simulator does not do that. `controller._best_for_tenant` REJECTS candidates
that would exceed the shared cap and leaves `best_state` at `current`, so a
starved tenant keeps the state it holds; and every candidate is within
`DELTA_REPLICAS = (-2, -1, 0, 1, 2)` of its interval-start state. Step 1
therefore modelled a strictly more punitive cluster than the one being
measured, and over-estimated the unavoidable violation.

This is the corrected derivation. `coupled_floor` and `fcfs_allocation` are
untouched; `incremental_allocation` and `coupled_floor_incremental` are new.

Two modelling choices decide whether this is a floor at all, and both are made
before any comparison is run:

  * **Foresight, exactly one ramp long.** A floor is what the BEST controller
    with this actuation authority can achieve. With a +-2 clamp, climbing from
    one replica to the ceiling of six takes three intervals, so each tenant
    provisions for the worst need inside a three-interval horizon. Without
    this the derivation scores a MYOPIC controller's catch-up cost as
    unavoidable, which it is not -- and it shows: the myopic variant returns
    an uncoupled floor of 0.125 where the published single-tenant derivation
    says 0.000000.
  * **Violation is scored against the need actually faced**, never against
    the target provisioned for. Holding capacity early is a cost to
    neighbours and not a credit to the holder, and that asymmetry is the
    coupling this derivation exists to price.

================================================================
THE VALIDATION READING, STATED BEFORE IT WAS COMPUTED
================================================================

A derivation over already-committed campaign data, so no pre-registration
applies (mirroring how `bec0f62` landed). The pre-stated reading is what keeps
it honest, and this file is committed before the comparison runs.

  S1 (soundness, must hold). The corrected floor must reduce EXACTLY to the
      published single-tenant floor of 0.000000 when the cap cannot bind --
      one tenant, and a cap at or above the maximum aggregate hold. A
      derivation failing this is wrong, not merely weak. Step 1's V1 passed
      this and so must its replacement.

  S2 (soundness, must hold). The multiset enumeration is exact only if the
      mean per-tenant violation does not depend on WHICH tenant holds which
      phase. The sweep is first-come-first-served by tenant id, so this is not
      obvious and is not assumed: `permutation_invariance_report` compares
      every distinct permutation of sampled offset multisets against its
      sorted representative. A non-zero spread forbids the multiset mode, and
      the record must then report the ordered enumeration cost instead of a
      number.

  S3 (the claim). The corrected floor is a floor on UNAVOIDABLE violation, so
      **every arm's measured `mean_violation` on the matching cell must be at
      or above it**. The binding case is `keda` at 0.001886, which is what
      falsified step 1. If any arm measures strictly below the corrected
      floor, this derivation is FALSIFIED and reported as such -- not
      adjusted, and not re-derived a third time inside this record.

  S4 (does it bite?, descriptive, never gates). Report the corrected floor
      against step 1's falsified 0.036487 and against the uncoupled 0.000000.
      If the corrected floor is ~0 the honest report is that the coupling is
      immaterial on this cell once the allocation is modelled correctly, and
      that the single-tenant derivation already describes it. That is a null
      result and is published as one.

No threshold is attached to S4 on purpose: it decides how the result is
described, never whether it is reported.

    python analysis_separation_mt_v2.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis_separation as sep  # noqa: E402
import analysis_separation_mt as mt  # noqa: E402  (cell + measured-violation reader)
from stats import record_path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
import guarantee  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from harness import workloads  # noqa: E402

STEP1_FLOOR = 0.036487  # RESULTS_SEPARATION_MT.md, the falsified derivation


def main() -> None:
    size = workloads.CLUSTER_SIZES[mt.CELL["cluster_size"]]
    tenants = len(workloads.TENANT_MIXES[mt.CELL["tenant_mix"]])
    cfg = mt.config_for(mt.CELL["cluster_size"])
    orbit = sep.flash_orbit()
    cap = size.limits_replicas

    started = time.time()
    invariance = guarantee.permutation_invariance_report(cfg, orbit, 4, cap)
    result = guarantee.coupled_floor_incremental(cfg, orbit, tenants, cap)
    single = guarantee.coupled_floor_incremental(cfg, orbit, 1, cap)
    slack = guarantee.coupled_floor_incremental(cfg, orbit, tenants,
                                                tenants * cfg.replica_max)
    elapsed = time.time() - started

    floor = result["coupled_violation"]
    measured = mt.measured_violations()

    L: list[str] = []
    w = L.append
    w("# The coupled floor, corrected — over the allocation the planner "
      "performs (WP13 step 2)\n")
    w("Generated by `analysis_separation_mt_v2.py` from "
      "`research/jcac_sim/guarantee.py`. Every number is computed at run time; "
      "nothing is transcribed.\n")
    w("**This record does not replace `RESULTS_SEPARATION_MT.md`.** That "
      "record derived a coupled floor, pre-stated the reading that would "
      "falsify it, and was falsified. It stands as committed, including its "
      "FAIL. This is the corrected derivation it names as the next step, with "
      "its own reading stated before the comparison was run.\n")

    w("\n## What was wrong, and what changed\n")
    w("Step 1's `fcfs_allocation` hands each tenant `min(need, remaining)` "
      "from zero, so a contended tenant is allocated nothing and scored at "
      "one replica. `controller._best_for_tenant` REJECTS over-cap candidates "
      "and leaves the tenant on the state it already holds, and every "
      "candidate is within `DELTA_REPLICAS = (-2, -1, 0, 1, 2)` of the "
      "interval-start state. Step 1 modelled a strictly more punitive cluster "
      "than the one being measured.\n")
    w("The corrected model gives each tenant exactly one ramp of foresight — "
      f"**{guarantee.ramp_lead(cfg.replica_max)} intervals**, the climb from "
      f"one replica to the ceiling of {cfg.replica_max} at two per interval — "
      "and scores violation against the need actually faced, never against "
      "the target provisioned for.\n")

    w("\n## S1 — soundness\n")
    w("| degenerate case | corrected floor | published single-tenant floor | "
      "identical? |")
    w("|---|---:|---:|---|")
    w(f"| one tenant | {single['coupled_violation']:.6f} | 0.000000 | "
      f"{'yes' if single['coupled_violation'] == 0.0 else '**NO**'} |")
    w(f"| cap at maximum aggregate hold | {slack['coupled_violation']:.6f} | "
      f"0.000000 | {'yes' if slack['coupled_violation'] == 0.0 else '**NO**'} |")
    s1 = single["coupled_violation"] == 0.0 and slack["coupled_violation"] == 0.0
    w("")
    w(f"**S1 {'PASS' if s1 else 'FAIL'}** — the corrected derivation "
      f"{'reduces exactly to' if s1 else 'does NOT reduce to'} the published "
      "single-tenant floor wherever the cap cannot bind.\n")

    w("\n## S2 — is the enumeration exact?\n")
    w(f"Mode walked: **{result['mode']}**, {result['states_walked']:,} "
      f"weighted states, {elapsed:.1f} s.\n")
    w(f"Permutation spread over {invariance['multisets_checked']} sampled "
      f"multisets: **{invariance['max_spread']:.2e}**.\n")
    s2 = invariance["invariant"] or result["mode"] == "ordered"
    w(f"**S2 {'PASS' if s2 else 'FAIL'}** — mean per-tenant violation "
      f"{'does not depend' if s2 else 'DEPENDS'} on which tenant holds which "
      "phase, so the multiset enumeration is "
      f"{'exact' if s2 else 'NOT exact and its number must not be used'}.\n")

    w("\n## S3 — the claim\n")
    w(f"Corrected coupled floor: **{floor:.6f}**\n")
    if measured is None:
        w("No committed campaign carries this cell, so S3 is **NOT "
          "EVALUABLE** — reported rather than silently skipped.\n")
        s3 = None
    else:
        w("| arm | measured `mean_violation` | at or above the floor? |")
        w("|---|---:|---|")
        s3 = True
        for _, row in measured.sort_values("mean_violation").iterrows():
            ok = row["mean_violation"] >= floor
            s3 = s3 and ok
            w(f"| `{row['system']}` | {row['mean_violation']:.6f} | "
              f"{'yes' if ok else '**NO**'} |")
        w("")
        w(f"**S3 {'PASS' if s3 else 'FAIL'}** — "
          + ("every arm measures at or above the corrected floor."
             if s3 else
             "an arm measures strictly below the corrected floor, so this "
             "derivation is FALSIFIED and is reported as such.") + "\n")

    w("\n## S4 — does it bite? (descriptive)\n")
    w("| derivation | floor |")
    w("|---|---:|")
    w(f"| uncoupled (no cap) | {result['uncoupled_violation']:.6f} |")
    w(f"| corrected, shared cap | **{floor:.6f}** |")
    w(f"| step 1, falsified | {STEP1_FLOOR:.6f} |")
    w("")
    if floor == 0.0:
        w("The corrected floor is **exactly zero**: once the allocation is "
          "modelled as the planner performs it, the cluster cap costs this "
          "cell nothing that a controller with this actuation authority could "
          "not avoid. The coupling is immaterial here, and the single-tenant "
          "derivation already describes the cell. That is a null result and "
          "is published as one.\n")
        w("It also quantifies how far step 1's allocation rule was off: the "
          f"whole of its {STEP1_FLOOR:.6f} was an artefact of starving "
          "contended tenants to one replica.\n")
    else:
        w(f"The corrected floor sits **{floor:.6f}** above the uncoupled one "
          f"and **{STEP1_FLOOR - floor:+.6f}** relative to step 1's falsified "
          "figure. Violation at or below the corrected floor on this cell is "
          "capacity, not control quality.\n")

    w("\n## Scope — what this record does NOT claim\n")
    w("- A floor on **violation**, not a cost separation. The cost side of "
      "`RESULTS_SEPARATION.md` is untouched and remains single-tenant.")
    w("- One cell (`flash_crud` / `uniform` / `small`), the one the orbit "
      "constructors mirror.")
    w("- The allocation rule is the simulator's own sweep, with its "
      "first-come-first-served order and its move clamp. A different "
      "contention rule gives a different floor, and a real cluster's "
      "scheduler is not this one.")
    w("- Foresight is exactly one ramp. A controller with a longer horizon is "
      "not modelled, and would not raise this floor.")
    w("- The retracted sign-agreement claim from M3 stays retracted; nothing "
      "here revives it.")

    out = record_path("RESULTS_SEPARATION_MT_V2.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"S1 {'PASS' if s1 else 'FAIL'} | S2 {'PASS' if s2 else 'FAIL'} | "
          f"S3 {s3} | floor {floor:.6f} | mode {result['mode']} "
          f"| {elapsed:.1f}s")


if __name__ == "__main__":
    main()
