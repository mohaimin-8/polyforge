#!/usr/bin/env python3
"""WP13 step 3 — the eight-tenant coupled floor, computed.

`RESULTS_SEPARATION_MT_V2.md` (step 2) corrected step 1's allocation rule and
then stopped: S3 was reported **NOT EVALUABLE** because the published cell has
eight tenants and the exact ordered walk is 16^7 = 268,435,456 offset vectors,
"beyond what this analysis will spend". The multiset shortcut that would have
avoided the walk is **invalid** (S2 FAIL: sweep order changes the answer), and
that finding stands unchanged here.

**What changed is the loop, not the model.** `_trajectory_violation` runs a
fixed number of branch-free steps per offset vector, so every trajectory can be
advanced in lockstep: `guarantee.trajectory_violation_batch` vectorises the
OFFSET-VECTOR axis while leaving the tenant sweep sequential, because
`others = sum(chosen) - chosen[i]` is exactly the order dependency S2 proved is
real. This is the same ordered enumeration, not a shortcut and not sampling,
and `BatchedOrderedWalkTests` pins it per-vector against the scalar walk at
caps that bind.

Nothing in `guarantee.py` above the WP13-step-3 block is modified. `coupled_floor`,
`fcfs_allocation`, `incremental_allocation` and `coupled_floor_incremental` are
untouched, so `RESULTS_SEPARATION_MT.md` and `RESULTS_SEPARATION_MT_V2.md` both
replay unchanged.

================================================================
THE READING, STATED BEFORE THE EIGHT-TENANT NUMBER EXISTED
================================================================

A derivation over already-committed campaign data, so no pre-registration
applies (mirroring `bec0f62` and v2). What keeps it honest is that this file is
committed before the walk finishes.

  S1 (soundness, must hold). Unchanged from v2: the floor must reduce EXACTLY
      to 0.000000 where the cap cannot bind. A derivation failing this is
      wrong, not merely weak.

  S2 (soundness, carried forward). v2 found a permutation spread of 0.171875
      at eight tenants, so the multiset enumeration stays forbidden. This
      record does not use it. S2 is re-run rather than cited, because a
      derivation that quietly depended on invariance would be wrong in exactly
      the way v2 caught.

  S5 (soundness, NEW — the artifact is not stale). The expensive rows live in
      a committed JSON because a 2.2-hour walk cannot sit inside
      `scripts/reproduce.py`. So this record RE-DERIVES the cheap rows at gate
      time and compares them to the artifact. Any mismatch is a FAIL and the
      record refuses to report the expensive rows.

  S3 (the claim). The corrected floor bounds UNAVOIDABLE violation, so **every
      arm's measured `mean_violation` on the matching cell must be at or above
      it**. If any arm measures strictly below, this derivation is FALSIFIED
      and reported as such -- not adjusted, and not re-derived a fourth time
      inside this record.

      **Predicted before the walk finished, and recorded here so it cannot be
      claimed afterwards:** S3 is expected to **FAIL**. The floor rises with
      tenant count -- 0.000000 for n<=4, 0.016514 at n=5, 0.049058 at n=6,
      0.090102 at n=7 -- while the binding measured arm, `keda`, sits at
      0.001886. The n=7 floor already exceeds it by ~48x and exceeds step 1's
      own falsified floor of 0.036487. An eight-tenant floor below 0.001886
      would require the series to reverse, and nothing in the first seven rows
      suggests it does.

      A FAIL here is not a retreat from v2's position; it is a stronger
      statement of it. v2 said the eight-tenant floor could not be computed.
      This says it can, and that the derivation does not bound the measured
      arms -- so the separation theorem is scoped to a single tenant **because
      the multi-tenant extension was tested and failed**, not because the
      arithmetic was too large.

  S4 (descriptive, never gates). Report the exact series and its shape.

    python separation_mt_v3_walk.py          # the expensive walk, once
    python analysis_separation_mt_v3.py      # this record, from the artifact
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis_separation as sep  # noqa: E402
import analysis_separation_mt as mt  # noqa: E402
from separation_mt_v3_walk import DEFAULT_OUT, cell  # noqa: E402
from stats import record_path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
import guarantee  # noqa: E402

STEP1_FLOOR = 0.036487   # RESULTS_SEPARATION_MT.md, falsified
V2_SPREAD = 0.171875     # RESULTS_SEPARATION_MT_V2.md, S2 FAIL
RECORD = "RESULTS_SEPARATION_MT_V3.md"
# Re-derived at gate time to prove the artifact is current. n<=6 is ~31 s;
# n=7 would add ~8 min and n=8 ~2.2 h, which is what the artifact exists for.
VERIFY_UPTO = 6


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--walk", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--verify-upto", type=int, default=VERIFY_UPTO)
    args = ap.parse_args()

    if not args.walk.exists():
        print(f"missing walk artifact {args.walk} — run "
              "`python separation_mt_v3_walk.py` first", file=sys.stderr)
        return 1
    walk = json.loads(args.walk.read_text(encoding="utf-8"))
    rows = {r["tenants"]: r for r in walk["rows"]}

    c = cell()
    cfg, orbit, cap, tenants = c["config"], c["orbit"], c["cap"], c["tenants"]

    # --- S5: the artifact is not stale ------------------------------------
    mismatches = []
    for n in range(1, min(args.verify_upto, walk["tenants_walked_to"]) + 1):
        fresh = guarantee.coupled_floor_incremental_batched(
            cfg, orbit, n, cap)["coupled_violation"]
        stored = rows[n]["coupled_violation"]
        if fresh != stored:
            mismatches.append((n, stored, fresh))
    s5 = not mismatches

    # --- S1: soundness ----------------------------------------------------
    single = guarantee.coupled_floor_incremental_batched(cfg, orbit, 1, cap)
    slack = guarantee.coupled_floor_incremental_batched(
        cfg, orbit, 3, 3 * cfg.replica_max)
    s1 = (single["coupled_violation"] == 0.0
          and slack["coupled_violation"] == 0.0)

    # --- S2: carried forward, re-run not cited ----------------------------
    invariance = guarantee.permutation_invariance_report(
        cfg, orbit, tenants, cap, samples=6)
    s2 = bool(invariance["invariant"])

    # --- S3: does the floor bound the measured arms? ----------------------
    cell_row = rows.get(tenants)
    measured = mt.measured_violations()
    per_arm = None
    below = []
    if cell_row is not None and measured is not None:
        per_arm = (measured.groupby("system").mean_violation.mean()
                   .sort_values())
        below = [(arm, v) for arm, v in per_arm.items()
                 if v < cell_row["coupled_violation"]]
    # Tri-state on purpose. A partial artifact, or a machine without the
    # campaign database, means S3 was NOT EVALUATED -- reporting that as FAIL
    # would manufacture a falsification out of a missing file, which is the
    # same class of error as v2's first permutation check passing vacuously.
    evaluable = cell_row is not None and measured is not None
    s3 = (not below) if evaluable else None

    def verdict(flag) -> str:
        return 'NOT EVALUATED' if flag is None else ('PASS' if flag else 'FAIL')

    L: list[str] = []
    w = L.append
    headline = ("computed, and it holds" if s3 else
                "computed, and falsified" if s3 is False else
                "not yet reached by the committed walk")
    w(f"# The eight-tenant coupled floor — {headline} (WP13 step 3)\n")
    w("Generated by `analysis_separation_mt_v3.py` from "
      "`research/jcac_sim/guarantee.py` and the committed walk artifact "
      f"`{args.walk.name}`. Every number is computed at run time or read from "
      "that artifact; nothing is transcribed.\n")
    w("**This record replaces no earlier one.** `RESULTS_SEPARATION_MT.md` "
      "(step 1, falsified) and `RESULTS_SEPARATION_MT_V2.md` (step 2, S3 not "
      "evaluable) stand exactly as committed. This is the step v2 named as "
      "next: the walk it could not afford.\n")

    w("\n## Headline\n")
    if cell_row is None:
        w("The walk artifact does not reach the published cell's tenant count, "
          "so S3 is not evaluated here.\n")
    else:
        w(f"The eight-tenant floor **is computable**: "
          f"**{cell_row['coupled_violation']:.6f}**, from an exact ordered "
          f"walk over **{cell_row['vectors']:,} offset vectors** in "
          f"**{cell_row['seconds'] / 60:.1f} minutes**. v2 reported this same "
          "quantity as NOT EVALUABLE; the obstacle was the enumeration loop, "
          "not the problem.\n")
        w(f"**S3 {verdict(s3)}.** " + (
            "Every measured arm sits at or above the floor.\n" if s3 else
            f"**{len(below)} arm(s) measure strictly below it**, so the "
            "corrected multi-tenant derivation is FALSIFIED — for the second "
            "time, now with the number in hand rather than out of reach. The "
            "separation theorem stays scoped to a single tenant **because the "
            "multi-tenant extension was tested and failed**, which is a "
            "different and stronger statement than v2's.\n"))

    w("\n## S5 — is the committed walk current?\n")
    slowest = max(r["seconds"] for r in rows.values())
    w("The expensive rows cannot live inside `scripts/reproduce.py`: the "
      f"largest walk in the artifact takes ~{slowest / 60:.0f} minutes "
      "and the gate re-runs every analysis. So they are computed once into "
      f"`{args.walk.name}` and the cheap rows are **re-derived here at gate "
      "time** and compared. A stale or edited artifact fails this check "
      "instead of passing quietly.\n")
    w(f"Rows re-derived: **1..{min(args.verify_upto, walk['tenants_walked_to'])}**. "
      f"**S5 {'PASS' if s5 else 'FAIL'}**"
      + ("" if s5 else f" — mismatches: {mismatches}") + ".\n")

    w("\n## S1 — soundness\n")
    w("| degenerate case | floor | published | identical? |")
    w("|---|---:|---:|---|")
    w(f"| one tenant | {single['coupled_violation']:.6f} | 0.000000 | "
      f"{'yes' if single['coupled_violation'] == 0.0 else '**NO**'} |")
    w(f"| three tenants, cap at their maximum hold | "
      f"{slack['coupled_violation']:.6f} | 0.000000 | "
      f"{'yes' if slack['coupled_violation'] == 0.0 else '**NO**'} |")
    w("")
    w(f"**S1 {'PASS' if s1 else 'FAIL'}** — computed through the batched walk, "
      "so this also re-checks that vectorising did not change the model.\n")

    w("\n## S2 — the multiset shortcut is still forbidden\n")
    w("Re-run rather than cited. This record does **not** use the multiset "
      "enumeration; it walks every ordered vector. S2 is reported because a "
      "derivation that quietly assumed invariance would be wrong in exactly "
      "the way v2 caught.\n")
    if invariance.get("vacuous"):
        w(f"**S2 VACUOUS** — {invariance['reason']}.\n")
    else:
        w(f"Largest spread across permutations of "
          f"{invariance['multisets_checked']} sampled offset multisets at "
          f"{tenants} tenants: **{invariance['max_spread']:.6f}** "
          f"(v2 measured {V2_SPREAD:.6f}).\n")
        w(f"**S2 {'PASS' if s2 else 'FAIL'}** — sweep order "
          f"{'does not change' if s2 else '**changes**'} the mean per-tenant "
          "violation. The ordered walk is unaffected either way; it is the "
          "cheap shortcut that this forbids.\n")

    w("\n## S3 — does the floor bound the measured arms?\n")
    if cell_row is None:
        w("Not evaluated: the artifact stops short of the cell.\n")
    elif measured is None:
        w("Not evaluated: the measured campaign database is not available "
          "(archive tier — see `docs/REPRODUCE.md`).\n")
    else:
        w(f"Floor at {tenants} tenants: **{cell_row['coupled_violation']:.6f}**\n")
        w("\n| arm | measured mean_violation | at or above the floor? |")
        w("|---|---:|---|")
        for arm, value in per_arm.items():
            ok = value >= cell_row["coupled_violation"]
            w(f"| `{arm}` | {value:.6f} | {'yes' if ok else '**NO**'} |")
        w("")
        w(f"**S3 {verdict(s3)}.**" + ("" if s3 else
          f" The binding arm is `{below[0][0]}` at {below[0][1]:.6f}, "
          f"{cell_row['coupled_violation'] / below[0][1]:.0f}x below the "
          "derived floor. Per the reading stated before the walk finished, "
          "this is reported and not repaired.\n"))
        if below:
            # A 0.1% miss and a 67x miss are not the same finding, and a single
            # FAIL verdict hides that. The shortfall ratio separates "the floor
            # is very slightly too high for this arm" from "the floor does not
            # describe this arm at all", and only the second one says the
            # derivation is modelling the wrong thing.
            w("\n### How far below, per arm (descriptive — does not gate)\n")
            w("| arm | measured | shortfall vs floor | reading |")
            w("|---|---:|---:|---|")
            for arm, value in below:
                ratio = cell_row["coupled_violation"] / value if value else float("inf")
                shortfall = 1.0 - value / cell_row["coupled_violation"]
                reading = ("within 1% of the floor — a calibration miss"
                           if shortfall < 0.01 else
                           "the floor does not describe this arm")
                w(f"| `{arm}` | {value:.6f} | {shortfall * 100:.2f}% "
                  f"({ratio:.1f}x) | {reading} |")
            w("")
            near = [a for a, v in below
                    if 1.0 - v / cell_row["coupled_violation"] < 0.01]
            far = [a for a, v in below
                   if 1.0 - v / cell_row["coupled_violation"] >= 0.01]
            if near and far:
                w(f"So the failure is not uniform: {', '.join(f'`{a}`' for a in near)} "
                  "sits essentially ON the floor, while "
                  f"{', '.join(f'`{a}`' for a in far)} sits orders of magnitude "
                  "under it. A floor that a reactive replica autoscaler meets to "
                  "within a fraction of a percent and an event-driven one misses "
                  "by two orders of magnitude is not merely mis-scaled — it is "
                  "pricing a contention the event-driven arm does not pay. "
                  "Naming that mechanism is future work and is NOT attempted "
                  "here; this record reports the falsification.\n")

    w("\n## S4 — the exact series (descriptive)\n")
    w("Every row is the ordered enumeration, exact, with no shortcut.\n")
    w("| tenants | offset vectors | cap can bind? | floor | walk seconds |")
    w("|---:|---:|---|---:|---:|")
    for n in sorted(rows):
        r = rows[n]
        w(f"| {n} | {r['vectors']:,} | "
          f"{'yes' if r['cap_can_bind'] else 'no'} | "
          f"{r['coupled_violation']:.6f} | {r['seconds']:.1f} |")
    w("")
    w(f"The floor is zero until the cap can bind at {cfg.replica_max}-replica "
      f"ceilings against a cap of {cap}, then rises monotonically. Step 1's "
      f"falsified floor was {STEP1_FLOOR:.6f}; the corrected derivation "
      f"crosses that between six and seven tenants, so the correction made "
      "the floor HIGHER at the published cell, not lower.\n")

    w("\n## What this changes for the thesis\n")
    w("v2's limitation was *\"the eight-tenant floor is not computed\"*. That "
      "sentence is now wrong and must not be repeated. The limitation is "
      + ("*\"the multi-tenant floor holds\"*.\n" if s3 else
         "*\"the multi-tenant floor was computed and does not bound the "
         "measured arms, so the separation result is stated for a single "
         "tenant\"*. The theorem's scope is unchanged; the reason for it is "
         "now a measurement rather than a budget.\n"))

    out = record_path(RECORD)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"S1 {'PASS' if s1 else 'FAIL'} | S2 {'PASS' if s2 else 'FAIL'} | "
          f"S3 {verdict(s3)} | S5 {'PASS' if s5 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
