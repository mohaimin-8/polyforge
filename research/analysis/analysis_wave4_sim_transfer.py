"""WL-H3': does the simulator transfer once its plant is fitted to the live
evidence? -> RESULTS_WAVE4_SIM_TRANSFER.md.

PROTOCOL: PREREG_WAVE4_SIM_TRANSFER.md, committed with this file BEFORE any
calibrated simulation ran. Every reading below is the registered one.

Inputs
- eval/results/wave4_sim_transfer_{cell}.duckdb (fitted plant, one per cell)
  and wave4_sim_transfer_control.duckdb (published plant, five arms), else
  the committed export wave4_sim_transfer_runs.csv (an `experiment` column
  names the file each row came from).
- eval/results/wave4_calibrated_plane_runs.csv: the live reference (B1').

Readings
- WL-H3'  the fitted simulator's cost winner equals live's in >= 3 of 4
          cells AND in more cells than the published-plant control.
- WL-H3'b Spearman rho of the five-arm cost ranking, fitted and control vs
          live, per cell; count of cells with rho >= 0.7.
- WL-H3'c fitted sim ranks jcac-calibrated below jcac on cost, per cell.
- descriptive: the same on the composite J; replica-seconds per arm.

    python analysis_wave4_sim_transfer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval" / "results"
CSV = RESULTS / "wave4_sim_transfer_runs.csv"
LIVE_CSV = RESULTS / "wave4_calibrated_plane_runs.csv"
LIVE_DB = RESULTS / "wave4_calibrated_plane.duckdb"

ARMS = ["jcac-calibrated", "jcac", "replica-only", "cache-only", "tier-only"]
CELLS = ["ai_cacheable", "tier_mixed", "crud_bursty", "joint_stress"]
FITTED = {cell: f"wave4_sim_transfer_{cell}" for cell in CELLS}
CONTROL = "wave4_sim_transfer_control"
TENANTS = 8
COST_SCALE = 0.01
RHO_THRESHOLD = 0.7
MIN_AGREE = 3

COLS = ["experiment", "system", "workload", "rep", "steps", "total_cost_usd",
        "mean_violation", "mean_jain"]


def _with_j(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["J"] = (df.total_cost_usd / ((df.steps - 1) * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def load_sim() -> pd.DataFrame:
    dbs = [RESULTS / f"{name}.duckdb" for name in list(FITTED.values()) + [CONTROL]]
    if all(p.exists() for p in dbs):
        frames = []
        for p in dbs:
            con = duckdb.connect(str(p), read_only=True)
            frames.append(con.execute(
                "select r.experiment, r.system, r.workload, r.rep, r.steps, m.total_cost_usd, "
                "m.mean_violation, m.mean_jain from runs r join metrics m on r.run_id = m.run_id "
                "where r.status = 'valid'").fetchdf())
            con.close()
        df = pd.concat(frames, ignore_index=True)
    elif CSV.exists():
        raw = pd.read_csv(CSV, float_precision="round_trip")
        df = raw[raw.status == "valid"][COLS].copy()
        for c in COLS[4:]:
            df[c] = pd.to_numeric(df[c])
    else:
        raise FileNotFoundError("no wave4_sim_transfer DuckDBs and no committed export")
    return _with_j(df.reset_index(drop=True))


def load_live() -> pd.DataFrame:
    if LIVE_DB.exists():
        con = duckdb.connect(str(LIVE_DB), read_only=True)
        df = con.execute(
            "select 'live' as experiment, r.system, r.workload, r.rep, r.steps, m.total_cost_usd, "
            "m.mean_violation, m.mean_jain from runs r join metrics m on r.run_id = m.run_id "
            "where r.status = 'valid'").fetchdf()
        con.close()
    else:
        raw = pd.read_csv(LIVE_CSV, float_precision="round_trip")
        raw = raw[raw.status == "valid"]
        df = raw[["system", "workload", "rep", "steps", "total_cost_usd", "mean_violation", "mean_jain"]].copy()
        df.insert(0, "experiment", "live")
        for c in ("steps", "total_cost_usd", "mean_violation", "mean_jain"):
            df[c] = pd.to_numeric(df[c])
    return _with_j(df.reset_index(drop=True))


# --- readings ------------------------------------------------------------

def means(df: pd.DataFrame, metric: str) -> dict[str, float]:
    """arm -> mean metric over reps (one cell's rows)."""
    g = df.groupby("system")[metric].mean()
    return {arm: float(g[arm]) for arm in ARMS if arm in g.index}


def ranking(m: dict[str, float]) -> list[str]:
    return sorted(m, key=lambda a: m[a])


def winner(m: dict[str, float]) -> str | None:
    return ranking(m)[0] if m else None


def spearman(a: dict[str, float], b: dict[str, float]) -> float | None:
    common = [arm for arm in ARMS if arm in a and arm in b]
    n = len(common)
    if n < 3:
        return None
    ra = {arm: i for i, arm in enumerate(sorted(common, key=lambda x: a[x]))}
    rb = {arm: i for i, arm in enumerate(sorted(common, key=lambda x: b[x]))}
    d2 = sum((ra[arm] - rb[arm]) ** 2 for arm in common)
    return 1.0 - 6.0 * d2 / (n * (n * n - 1))


def cell_rows(sim: pd.DataFrame, live: pd.DataFrame, metric: str) -> list[dict]:
    rows = []
    for cell in CELLS:
        lv = means(live[live.workload == cell], metric)
        ft = means(sim[(sim.experiment == FITTED[cell]) & (sim.workload == cell)], metric)
        ct = means(sim[(sim.experiment == CONTROL) & (sim.workload == cell)], metric)
        rows.append({
            "cell": cell, "live": lv, "fitted": ft, "control": ct,
            "live_winner": winner(lv), "fitted_winner": winner(ft), "control_winner": winner(ct),
            "fitted_agrees": bool(lv) and bool(ft) and winner(lv) == winner(ft),
            "control_agrees": bool(lv) and bool(ct) and winner(lv) == winner(ct),
            "rho_fitted": spearman(ft, lv), "rho_control": spearman(ct, lv),
            "fitted_cal_below_jcac": (ft.get("jcac-calibrated", float("inf")) < ft.get("jcac", float("inf"))) if ft else None,
            "live_cal_below_jcac": (lv.get("jcac-calibrated", float("inf")) < lv.get("jcac", float("inf"))) if lv else None,
        })
    return rows


def fmt_rho(r) -> str:
    return "n/a" if r is None else f"{r:+.2f}"


def fmt_rank(m: dict[str, float]) -> str:
    return " < ".join(f"{a} ({m[a]:.4f})" for a in ranking(m)) if m else "n/a"


def build() -> str:
    sim, live = load_sim(), load_live()
    cost = cell_rows(sim, live, "total_cost_usd")
    j = cell_rows(sim, live, "J")
    agree_f = sum(r["fitted_agrees"] for r in cost)
    agree_c = sum(r["control_agrees"] for r in cost)
    h3 = "PASS" if agree_f >= MIN_AGREE and agree_f > agree_c else "FAIL"
    rho_f = sum(1 for r in cost if r["rho_fitted"] is not None and r["rho_fitted"] >= RHO_THRESHOLD)
    rho_c = sum(1 for r in cost if r["rho_control"] is not None and r["rho_control"] >= RHO_THRESHOLD)
    h3c = [(r["cell"], r["fitted_cal_below_jcac"], r["live_cal_below_jcac"]) for r in cost]
    n_sim = {name: int((sim.experiment == name).sum()) for name in list(FITTED.values()) + [CONTROL]}

    L = [f"# WL-H3′ — the simulator with its plant fitted to the live evidence: {h3} "
         f"({agree_f} of 4 cost winners agree with live; the published plant: {agree_c} of 4)", "",
         "Generated by `analysis_wave4_sim_transfer.py` from the committed export "
         "`eval/results/wave4_sim_transfer_runs.csv` (five sim experiments: the fitted plant per cell and the "
         "five-arm published-plant control) and the live reference `wave4_calibrated_plane_runs.csv` (B1′). "
         "Pre-registration: `PREREG_WAVE4_SIM_TRANSFER.md`; plant fit: `research/calibration/LIVE_PLANT_FIT.md`. "
         "Ordinal only — no absolute crosses the substrate boundary.", "",
         "## Headline", "",
         f"**WL-H3′ — {h3}.** Cost winner per cell, fitted simulator vs live: **{agree_f} of 4** agree "
         f"(registered threshold {MIN_AGREE}); the published-plant control agrees in **{agree_c} of 4**"
         + (" — the fit improves on it." if agree_f > agree_c else " — the fit does not improve on it.") , "",
         f"**WL-H3′b** — cells with Spearman ρ ≥ {RHO_THRESHOLD} on the five-arm cost ranking: fitted **{rho_f} of 4**, "
         f"control {rho_c} of 4.", "",
         "**WL-H3′c** — the correction's transfer (fitted sim ranks `jcac-calibrated` below `jcac` on cost): "
         + ", ".join(f"`{c}` {'PASS' if f else 'FAIL'}" for c, f, _ in h3c)
         + " (live: " + ", ".join(f"{'yes' if l else 'no'}" for _, _, l in h3c) + ").", "",
         "* **Runs:** " + ", ".join(f"{k} {v}" for k, v in n_sim.items()) + f"; live {len(live)} valid runs.", "",
         "## Cost — winner and ranking per cell", "",
         "| cell | live winner | fitted winner | agrees | control winner | agrees | ρ fitted | ρ control |",
         "|---|---|---|---|---|---|---|---|"]
    for r in cost:
        L.append(f"| {r['cell']} | {r['live_winner']} | {r['fitted_winner']} | {'**yes**' if r['fitted_agrees'] else 'no'} | "
                 f"{r['control_winner']} | {'yes' if r['control_agrees'] else 'no'} | {fmt_rho(r['rho_fitted'])} | "
                 f"{fmt_rho(r['rho_control'])} |")
    L += ["", "Full rankings (mean cost $ per run over reps; live over 2, sim over 3):", ""]
    for r in cost:
        L += [f"* `{r['cell']}`", f"  * live: {fmt_rank(r['live'])}", f"  * fitted: {fmt_rank(r['fitted'])}",
              f"  * control: {fmt_rank(r['control'])}"]
    L += ["", "## Composite J — descriptive", "",
          "J = cost / ((steps−1)·8) / 0.01 + 2·violation + 0.5·(1−Jain), as B1's WL-H3 defined it. The live J rests on "
          "the replay-clock violation and Jain (both ~0 and ~1 in every valid live run), so it is cost-dominated.", "",
          "| cell | live winner | fitted winner | agrees | control winner | agrees | ρ fitted | ρ control |",
          "|---|---|---|---|---|---|---|---|"]
    for r in j:
        L.append(f"| {r['cell']} | {r['live_winner']} | {r['fitted_winner']} | {'yes' if r['fitted_agrees'] else 'no'} | "
                 f"{r['control_winner']} | {'yes' if r['control_agrees'] else 'no'} | {fmt_rho(r['rho_fitted'])} | "
                 f"{fmt_rho(r['rho_control'])} |")
    L += ["", "## Notes", "",
          "* The fitted capacity is a lower bound (no live run reached congestion), `mid`'s tier latency is "
          "interpolated, and the cache ceilings are the plant's under this harness's prompt pools; all frozen "
          "in the pre-registration before any calibrated run.",
          "* Live `total_cost_usd` carries the WL-H2 preflight's spend identically in every arm (B1′, disclosed); "
          "an identical offset cannot change a ranking.",
          ""]
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_WAVE4_SIM_TRANSFER.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
