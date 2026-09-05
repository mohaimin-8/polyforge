"""Phase 7 acceptance figure: does the sim's jcac-vs-hpa ranking reproduce live?

PROTOCOL — FROZEN BEFORE THE LIVE SLICE FINISHED (2026-07-13, session 17;
committed while phase7_live.yaml was still executing on the Codespace, so
no live number had been seen). It implements exactly the pre-declared
reading (V2_README Phase 7, PHASE7_JCAC_PLAN.md, ground rule 4):

- Inputs: `eval/results/phase7_live.duckdb` (cluster backend, 2 systems x
  2 workload classes x 3 reps) and `eval/results/phase7_sim_ref.duckdb`
  (the identical cells on the sim backend).
- Per substrate, per cell (workload class), per system: mean over reps of
  J, cost, and violation, with min/max across reps shown for spread.
  J = total_cost_usd / ((steps-1) * 8 tenants) / 0.01 + 2*violation +
  0.5*(1-jain) — the paper's stated objective (stats.composite_objective),
  computed within each substrate separately.
- ORDINAL READING (the only claim): for each cell and metric, the winner
  (lower is better) in sim vs the winner live. Primary: the J winner per
  cell. Secondary/descriptive: cost and violation winners. Agreement =
  same winner both substrates.
- NO absolute cross-substrate comparison, NO significance tests (3 reps
  per arm is a spread check, not a sample), NO pooling across cells.
- The figure caption is frozen verbatim in PHASE7_JCAC_PLAN.md step 3 and
  is reproduced in the output.

One run, results to PHASE7_ORDINAL.md; re-running after seeing results
requires a declared amendment.

    python phase7_ordinal.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from stats import record_path

# Two arms whose means differ by less than this are tied, and a tie yields no
# ordinal reading. Without it, idxmin() breaks ties alphabetically and the
# arbitrary winner is published as an AGREE/DISAGREE finding.
TIE_EPS = 1e-9

ROOT = Path(__file__).resolve().parents[2]
LIVE_DB = ROOT / "eval" / "results" / "phase7_live.duckdb"
SIM_DB = ROOT / "eval" / "results" / "phase7_sim_ref.duckdb"
# Committed exports so this record rebuilds on a clean clone (the
# DuckDBs are gitignored and Zenodo-archived).
LIVE_CSV = ROOT / "eval" / "results" / "phase7_live_runs.csv"
SIM_CSV = ROOT / "eval" / "results" / "phase7_sim_ref_runs.csv"
TENANTS = 8
COST_SCALE = 0.01


def load(db: Path, label: str, csv: Path | None = None) -> pd.DataFrame:
    """Rows from the DuckDB, else from the committed csv export.

    The DuckDBs are gitignored and Zenodo-archived, so without the fallback
    this record sat outside `reproduce.py`'s gate: it could not be rebuilt on
    a clean clone at all. The export carries `status`, so the same
    `valid`-only filter applies on both paths — which is what makes the
    retro-invalidated zero-telemetry rows stay excluded either way.
    """
    if db.exists():
        con = duckdb.connect(str(db), read_only=True)
        df = con.execute(
            "select r.system, r.workload, r.rep, m.total_cost_usd, "
            "m.mean_violation, m.mean_jain, m.steps "
            "from runs r join metrics m on r.run_id = m.run_id "
            "where r.status = 'valid'"
        ).fetchdf()
        con.close()
    elif csv is not None and csv.exists():
        raw = pd.read_csv(csv, float_precision="round_trip")
        raw = raw[raw.status == "valid"]
        df = pd.DataFrame({
            "system": raw.system, "workload": raw.workload, "rep": raw.rep,
            "total_cost_usd": pd.to_numeric(raw.total_cost_usd),
            "mean_violation": pd.to_numeric(raw.mean_violation),
            "mean_jain": pd.to_numeric(raw.mean_jain),
            "steps": pd.to_numeric(raw.metric_steps),
        }).reset_index(drop=True)
    else:
        raise FileNotFoundError(
            f"{db.name} missing and no committed export to fall back to")
    df["J"] = (df.total_cost_usd / ((df.steps - 1) * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    df["substrate"] = label
    return df


def main() -> None:
    live = load(LIVE_DB, "live", LIVE_CSV)
    sim = load(SIM_DB, "sim", SIM_CSV)
    frames = pd.concat([sim, live], ignore_index=True)

    lines = ["# Phase 7 ordinal figure — sim ranking vs live ranking", ""]
    w = lines.append
    w("Protocol frozen in `phase7_ordinal.py` before the live slice "
      "finished. Ordinal reading only (ground rule 4): per cell, does the "
      "substrate-internal winner agree? Absolutes are never compared "
      "across substrates.")
    w("")

    metrics = [("J", "J"), ("total_cost_usd", "cost"), ("mean_violation", "violation")]
    agreements: dict[str, list[str]] = {name: [] for _, name in metrics}
    for workload in sorted(frames.workload.unique()):
        w(f"## Cell: {workload}")
        w("")
        w("| substrate | system | J mean [min..max] | cost $ mean | violation mean |")
        w("|---|---|---|---|---|")
        cell = frames[frames.workload == workload]
        for substrate in ("sim", "live"):
            for system in ("jcac", "hpa"):
                s = cell[(cell.substrate == substrate) & (cell.system == system)]
                if s.empty:
                    w(f"| {substrate} | {system} | (missing) | — | — |")
                    continue
                w(f"| {substrate} | {system} | {s.J.mean():.3f} "
                  f"[{s.J.min():.3f}..{s.J.max():.3f}] | "
                  f"{s.total_cost_usd.mean():.2f} | {s.mean_violation.mean():.4f} |")
        w("")
        for col, name in metrics:
            winners = {}
            for substrate in ("sim", "live"):
                sub = cell[cell.substrate == substrate]
                means = sub.groupby("system")[col].mean()
                if len(means) != 2:
                    winners[substrate] = "(incomplete)"
                elif abs(means.iloc[0] - means.iloc[1]) < TIE_EPS:
                    # An exact tie was previously resolved by idxmin(), which
                    # returns the alphabetically first index — so a live cell
                    # where both arms scored identically (violation 0.0000 for
                    # every arm, which is what the phase7 load produced)
                    # published `hpa` as the "winner" and an AGREE/DISAGREE
                    # verdict decided by the letter h preceding j.
                    winners[substrate] = "(tie)"
                else:
                    winners[substrate] = means.idxmin()
            tied = "(tie)" in winners.values() or "(incomplete)" in winners.values()
            agree = (not tied) and winners["sim"] == winners["live"]
            verdict = "TIE — no ordinal reading" if tied else (
                "AGREE" if agree else "DISAGREE")
            agreements[name].append(f"{workload}: {verdict}")
            w(f"- **{name} winner** — sim: `{winners['sim']}`, live: "
              f"`{winners['live']}` -> **{verdict}**")
        w("")

    w("## Verdict (primary reading = J winner per cell)")
    w("")
    for name, results in agreements.items():
        w(f"- {name}: " + "; ".join(results))
    w("")
    w("Frozen caption (PHASE7_JCAC_PLAN.md step 3):")
    w("")
    w("> Live ordinal check on a kind cluster: does the simulator's "
      "jcac-vs-HPA ranking on J/cost/violation reproduce under a real "
      "scheduler, real HPA, and real load? Absolutes are not comparable "
      "across substrates and are not compared. The live data plane burns "
      "fixed CPU per request kind, so the cache-size and model-tier knobs "
      "are inert here: this figure validates the replica-control "
      "projection of the joint controller. The cache knob's realism is "
      "carried by the LMSYS protocol (SEMANTIC_CACHE.md, "
      "CACHE_PRECISION.md), the tier knob's by the GPU tier bench "
      "(TIER_BENCH.md).")
    # record_path honours $POLYFORGE_ANALYSIS_OUT, which is how
    # scripts/reproduce.py redirects a rebuild away from the frozen committed
    # record. Writing to a hardcoded path meant the gate overwrote the very
    # file it was checking and then had nothing to compare against — so this
    # record could never actually be verified.
    out = record_path("PHASE7_ORDINAL.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
