"""B1', the corrected joint controller on the live plane -> RESULTS_WAVE4_CALIBRATED.md.

PROTOCOL: PREREG_WAVE4_CALIBRATED.md, committed with this file BEFORE the
run. Every reading below is the registered one; nothing is chosen after
seeing the numbers.

Inputs
- eval/results/wave4_calibrated_plane.duckdb (gitignored, Zenodo-archived),
  else the committed export wave4_calibrated_plane_runs.csv: 5 arms x 4
  cells x 2 reps on the cluster backend.
- eval/results/wave4_calibrated_plane_evidence/runs/<arm>__<cell>__uniform__small__rep<N>/
  eval-export-fine.json (per-10-s-bucket cost_usd, the bootstrap's unit) and
  metrics_histogram.json (the clause-4 route-level p95/p99).
- run_logs/matrix.log for the harness audit and the WL-H2 count; host_facts.

Readings
- WL-H1' (primary): in joint_stress, jcac-calibrated beats EVERY other arm
  (incl. jcac) on mean cost at Jain no worse by more than MARGIN, and the
  paired bootstrap 95% CI on per-bucket cost deltas (buckets paired by
  position, reps pooled, N_BOOT resamples, SEED) excludes 0. Any comparison
  not won, or any CI touching 0, is FAIL.
- WL-H4 (secondary): per cell, calibrated mean cost <= min(cache-only,
  tier-only) at iso-fairness; CI reported; "no separation" when the CI
  includes 0 even if the point estimate wins.
- WL-H5 (descriptive): calibrated vs jcac deltas per cell with CIs.
- WL-H2: PASS count from the matrix log.

    python analysis_wave4_calibrated.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval" / "results"
DB, CSV = RESULTS / "wave4_calibrated_plane.duckdb", RESULTS / "wave4_calibrated_plane_runs.csv"
EVIDENCE = RESULTS / "wave4_calibrated_plane_evidence"

ARMS = ["jcac-calibrated", "jcac", "replica-only", "cache-only", "tier-only"]
CELLS = ["ai_cacheable", "tier_mixed", "crud_bursty", "joint_stress"]
PRIMARY_CELL = "joint_stress"
TREATMENT = "jcac-calibrated"
MARGIN = 0.01
N_BOOT = 10_000
SEED = 20260915

COLS = ["system", "workload", "rep", "total_cost_usd", "mean_violation", "mean_jain",
        "cache_hit_rate", "crud_p95_ms", "ai_p95_ms", "crud_p99_ms", "ai_p99_ms"]


def load() -> pd.DataFrame:
    if DB.exists():
        con = duckdb.connect(str(DB), read_only=True)
        df = con.execute(
            "select r.system, r.workload, r.rep, m.total_cost_usd, m.mean_violation, m.mean_jain, "
            "m.cache_hit_rate, m.crud_p95_ms, m.ai_p95_ms, m.crud_p99_ms, m.ai_p99_ms "
            "from runs r join metrics m on r.run_id = m.run_id where r.status = 'valid'").fetchdf()
        con.close()
    elif CSV.exists():
        raw = pd.read_csv(CSV, float_precision="round_trip")
        raw = raw[raw.status == "valid"]
        df = raw[COLS].copy()
        for c in COLS[3:]:
            df[c] = pd.to_numeric(df[c])
    else:
        raise FileNotFoundError(f"{DB.name} missing and no committed export {CSV.name}")
    return df.reset_index(drop=True)


# --- per-run evidence ---------------------------------------------------

def run_dir(arm: str, cell: str, rep: int) -> Path:
    return EVIDENCE / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"


def bucket_costs(arm: str, cell: str, rep: int) -> list[float] | None:
    p = run_dir(arm, cell, rep) / "eval-export-fine.json"
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    b = doc.get("buckets") or []
    if not b or "cost_usd" not in b[0]:
        return None
    return [float(x["cost_usd"]) for x in b]


def histogram_p95(arm: str, cell: str, rep: int) -> dict:
    """route -> (p95_ms, p99_ms) from the in-run scrape; {} when absent/errored."""
    p = run_dir(arm, cell, rep) / "metrics_histogram.json"
    if not p.exists():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {k: (v["p95_ms"], v["p99_ms"]) for k, v in (doc.get("routes") or {}).items()}


def paired_deltas(cell: str, a: str, b: str, reps: list[int]) -> list[float]:
    """Per-bucket cost deltas a - b, paired by bucket position, reps pooled.
    A rep contributes only if both arms carry priced buckets of equal count."""
    out = []
    for rep in reps:
        ca, cb = bucket_costs(a, cell, rep), bucket_costs(b, cell, rep)
        if ca and cb and len(ca) == len(cb):
            out.extend(x - y for x, y in zip(ca, cb))
    return out


def bootstrap_ci(deltas: list[float], n_boot: int = N_BOOT, seed: int = SEED) -> tuple[float, float] | None:
    if len(deltas) < 2:
        return None
    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(sum(rng.choice(deltas) for _ in range(n)) / n for _ in range(n_boot))
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def matrix_audit() -> tuple[dict, int]:
    p = EVIDENCE / "run_logs" / "matrix.log"
    if not p.exists():
        return {}, 0
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"experiment\"[^{}]*\}", text, re.S)
    return (json.loads(m.group(0)) if m else {}), len(re.findall(r"verdict: WL-H2 PASS", text))


def host() -> dict:
    p = EVIDENCE / "host_facts.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# --- readings ------------------------------------------------------------

def means(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby(["workload", "system"], as_index=False)[
        ["total_cost_usd", "mean_jain", "mean_violation", "cache_hit_rate", "ai_p95_ms", "crud_p95_ms"]].mean()


def compare(df: pd.DataFrame, cell: str, arm: str) -> dict:
    m = means(df)
    reps = sorted(df.rep.unique())
    t = m[(m.workload == cell) & (m.system == TREATMENT)].iloc[0]
    o = m[(m.workload == cell) & (m.system == arm)].iloc[0]
    d_cost = float(t.total_cost_usd - o.total_cost_usd)
    d_jain = float(t.mean_jain - o.mean_jain)
    deltas = paired_deltas(cell, TREATMENT, arm, reps)
    ci = bootstrap_ci(deltas)
    excludes0 = ci is not None and (ci[1] < 0 or ci[0] > 0)
    return {"arm": arm, "cost": float(o.total_cost_usd), "jain": float(o.mean_jain),
            "d_cost": d_cost, "d_cost_pct": 100.0 * d_cost / float(o.total_cost_usd),
            "d_jain": d_jain, "iso": d_jain >= -MARGIN, "n_pairs": len(deltas),
            "ci": ci, "ci_excludes_0": excludes0,
            "beats": d_cost < 0 and d_jain >= -MARGIN and excludes0 and ci[1] < 0}


def fmt_ci(ci) -> str:
    return "n/a" if ci is None else f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"


def build() -> str:
    df = load()
    audit, h2 = matrix_audit()
    hf = host(); gpu = (hf.get("gpus") or [{}])[0]
    m = means(df)
    present = set(df.system.unique())
    missing_arms = [a for a in ARMS if a not in present]

    # WL-H1'
    h1 = [compare(df, PRIMARY_CELL, a) for a in ARMS if a != TREATMENT and a in present]
    h1_verdict = "PASS" if h1 and all(r["beats"] for r in h1) and not missing_arms else "FAIL"
    # WL-H4
    h4 = []
    for cell in CELLS:
        rows = [compare(df, cell, a) for a in ("cache-only", "tier-only") if a in present]
        if not rows:
            continue
        best = min(rows, key=lambda r: r["cost"])
        if best["d_cost"] < 0 and best["iso"] and best["ci_excludes_0"]:
            verdict = "PASS"
        elif best["d_cost"] <= 0 and best["iso"]:
            verdict = "no separation"
        else:
            verdict = "FAIL"
        h4.append((cell, best, verdict))
    # WL-H5
    h5 = [(cell, compare(df, cell, "jcac")) for cell in CELLS if "jcac" in present]

    t_primary = m[(m.workload == PRIMARY_CELL) & (m.system == TREATMENT)].iloc[0]
    L = []
    L.append(f"# B1′ — the corrected joint controller on the live plane: WL-H1′ {h1_verdict} in `{PRIMARY_CELL}`")
    L.append("")
    L.append("Generated by `analysis_wave4_calibrated.py` from `eval/results/wave4_calibrated_plane_runs.csv` "
             "(the committed export of `wave4_calibrated_plane.duckdb`) and the per-run evidence under "
             "`eval/results/wave4_calibrated_plane_evidence/runs/`. Pre-registration: `PREREG_WAVE4_CALIBRATED.md`, "
             "committed with this scorer before the run.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append(f"**WL-H1′ — {h1_verdict}.** `{TREATMENT}` in `{PRIMARY_CELL}`: mean cost "
             f"**${t_primary.total_cost_usd:.4f}**, Jain {t_primary.mean_jain:.3f}, over {len(sorted(df.rep.unique()))} rep(s). "
             f"Beats {sum(r['beats'] for r in h1)} of {len(h1)} comparators on cost at iso-fairness with a paired-bootstrap "
             f"CI excluding 0" + (f"; arms missing from the data: {missing_arms}." if missing_arms else "."))
    L.append("")
    L.append(f"**WL-H2 — {h2} PASS verdicts** in the matrix log; harness audit: expected {audit.get('expected_runs', '?')}, "
             f"valid {audit.get('valid_runs', '?')}, failed {audit.get('failed_runs', '?')}, ok {audit.get('ok', '?')}.")
    L.append("")
    L.append("**WL-H4 — ablation dominance per cell:** " + "; ".join(f"`{c}` {v}" for c, _, v in h4) + ".")
    L.append("")
    L.append(f"* **Host:** {hf.get('cpu_count', '?')} vCPU, {hf.get('mem_total_gib', '?')} GiB, {gpu.get('name', '?')} "
             f"{gpu.get('memory_total', '?')}, driver {gpu.get('driver', '?')} (`host_facts.json`).")
    L.append("")
    L.append(f"## WL-H1′ — `{PRIMARY_CELL}`, `{TREATMENT}` vs every other arm")
    L.append("")
    L.append("Beats = lower mean cost AND Jain within the 0.01 margin AND the paired bootstrap 95% CI on the per-bucket "
             f"cost delta (calibrated − arm; 10-s buckets paired by position; reps pooled; {N_BOOT} resamples, seed {SEED}) "
             "lies entirely below 0.")
    L.append("")
    L.append("| vs arm | arm cost $ | Δ cost | Δ % | Δ Jain | iso-fair | pairs | 95% CI on Δ/bucket | beats |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in h1:
        L.append(f"| {r['arm']} | {r['cost']:.4f} | {r['d_cost']:+.4f} | {r['d_cost_pct']:+.1f}% | {r['d_jain']:+.3f} | "
                 f"{'yes' if r['iso'] else 'no'} | {r['n_pairs']} | {fmt_ci(r['ci'])} | **{'yes' if r['beats'] else 'NO'}** |")
    L.append("")
    L.append("## WL-H4 — ablation dominance, every cell")
    L.append("")
    L.append("| cell | cheaper ablation | its cost $ | calibrated Δ cost | Δ Jain | 95% CI on Δ/bucket | verdict |")
    L.append("|---|---|---|---|---|---|---|")
    for cell, best, verdict in h4:
        L.append(f"| {cell} | {best['arm']} | {best['cost']:.4f} | {best['d_cost']:+.4f} | {best['d_jain']:+.3f} | "
                 f"{fmt_ci(best['ci'])} | **{verdict}** |")
    L.append("")
    L.append("## WL-H5 — the correction's measured effect (calibrated vs published jcac)")
    L.append("")
    L.append("| cell | jcac cost $ | Δ cost | Δ % | Δ Jain | 95% CI on Δ/bucket |")
    L.append("|---|---|---|---|---|---|")
    for cell, r in h5:
        L.append(f"| {cell} | {r['cost']:.4f} | {r['d_cost']:+.4f} | {r['d_cost_pct']:+.1f}% | {r['d_jain']:+.3f} | {fmt_ci(r['ci'])} |")
    L.append("")
    L.append("## Every run — means over reps (replay-clock latencies) and the histogram's route p95 / p99")
    L.append("")
    L.append("| cell | arm | cost $ | Jain | violation | cache hit | AI p95 ms (replay) | CRUD p95 ms (replay) | histogram replay-route p95 / p99 ms |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    order = {c: i for i, c in enumerate(CELLS)}; aorder = {a: i for i, a in enumerate(ARMS)}
    for _, r in m.assign(_c=m.workload.map(order), _a=m.system.map(aorder)).sort_values(["_c", "_a"]).iterrows():
        hp = []
        for rep in sorted(df.rep.unique()):
            routes = histogram_p95(r.system, r.workload, int(rep))
            replay = next((v for k, v in routes.items() if "replay" in k), None)
            if replay:
                hp.append(f"{replay[0]:.1f}/{replay[1]:.1f}")
        L.append(f"| {r.workload} | {r.system} | {r.total_cost_usd:.4f} | {r.mean_jain:.3f} | {r.mean_violation:.4f} | "
                 f"{r.cache_hit_rate:.2f} | {r.ai_p95_ms:.0f} | {r.crud_p95_ms:.1f} | {' ; '.join(hp) or 'not captured'} |")
    L.append("")
    L.append("## Notes")
    L.append("")
    L.append("* Histogram p95/p99 are route-level (the metric has no tenant label) and are never compared to the replay clock.")
    L.append("* Missing bucket costs (a run whose fine export lacks `cost_usd`) drop that rep from the pairing; `pairs` says how many buckets each CI rests on.")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_WAVE4_CALIBRATED.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
