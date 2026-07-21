"""Learned-joint-control campaign analysis (PREREG_LEARNED_CONTROL.md, frozen).

Reads the matrix_learned duckdb, scores the frozen readings — LR-H1
(non-inferiority of the MPC to the offline-trained learned policy,
confirmatory), LR-H2 (data-efficiency: trained beats online, confirmatory),
LR-D1 (descriptive) — and writes RESULTS_LEARNED.md. Pairing is the standard
matched-cells design: (workload, tenant_mix, cluster_size, rep).

    python analysis_learned.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats as sps

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "eval" / "results" / "raw_sim_learned.duckdb"
OUT = Path(__file__).resolve().parent / "RESULTS_LEARNED.md"
TENANTS = 8
COST_SCALE = 0.01
CELL = ["workload", "tenant_mix", "cluster_size", "rep"]
NI_MARGIN_FRAC = 0.05  # LR-H1: delta = 5% of the learned policy's mean J (frozen)
BOOT_N = 10_000
BOOT_SEED = 20260721

LABELS = {
    "jcac_anchored": "PolyForge MPC (anchored)",
    "learned_trained": "Learned joint (offline-trained, frozen-greedy)",
    "learned_online": "Learned joint (online ablation)",
    "hpa": "HPA",
}
ORDER = ["jcac_anchored", "learned_trained", "learned_online", "hpa"]


def load() -> pd.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(
        "select r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
        "m.total_cost_usd, m.mean_violation, m.mean_jain, m.steps "
        "from runs r join metrics m on r.run_id = m.run_id "
        "where r.status = 'valid'"
    ).fetchdf()
    con.close()
    # Identical to stats.composite_objective and every matrix campaign.
    df["J"] = (df.total_cost_usd / (119 * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def _paired_diff(df: pd.DataFrame, a: str, b: str, metric: str):
    m = df[df.system == a].merge(df[df.system == b], on=CELL, suffixes=("_a", "_b"))
    return m[f"{metric}_a"] - m[f"{metric}_b"], m


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    diff, m = _paired_diff(df, a, b, metric)
    sd = diff.std(ddof=1)
    base = {"mean_diff": float(diff.mean()) if len(diff) else 0.0,
            "n": len(diff),
            "mean_a": float(m[f"{metric}_a"].mean()) if len(m) else 0.0,
            "mean_b": float(m[f"{metric}_b"].mean()) if len(m) else 0.0}
    if len(diff) < 2 or sd == 0.0:
        return {**base, "p": 1.0, "dz": 0.0}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {**base, "p": float(p), "dz": float(diff.mean() / sd)}


def bootstrap_ci(diff: pd.Series, n: int = BOOT_N) -> tuple[float, float]:
    """95% bootstrap CI of the paired mean difference (the citable unit at
    this n, per the EFFECT_SIZES rule)."""
    if len(diff) < 2:
        return (0.0, 0.0)
    rng = np.random.default_rng(BOOT_SEED)
    vals = diff.to_numpy()
    means = rng.choice(vals, size=(n, len(vals)), replace=True).mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def non_inferiority(df: pd.DataFrame, a: str, b: str, metric: str, frac: float) -> dict:
    """LR-H1: is `a` non-inferior to `b` within delta = frac * mean(b)?

    Lower J is better, so non-inferiority means mean(a) - mean(b) < delta.
    One-sided paired t-test of (diff - delta) against 0, alternative 'less'.
    """
    diff, m = _paired_diff(df, a, b, metric)
    mean_b = float(m[f"{metric}_b"].mean()) if len(m) else 0.0
    delta = frac * abs(mean_b)
    sd = diff.std(ddof=1)
    if len(diff) < 2 or sd == 0.0:
        p = 1.0
    else:
        _, p = sps.ttest_1samp(diff - delta, 0.0, alternative="less")
    lo, hi = bootstrap_ci(diff)
    return {"n": len(diff), "delta": delta, "mean_diff": float(diff.mean()),
            "p": float(p), "dz": float(diff.mean() / sd) if sd else 0.0,
            "ci_lo": lo, "ci_hi": hi,
            "mean_a": float(m[f"{metric}_a"].mean()) if len(m) else 0.0,
            "mean_b": mean_b}


def md_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |"
              for _, r in frame.iterrows()]
    return "\n".join(lines)


def main() -> None:
    runs = load()
    lines: list[str] = []
    w = lines.append
    w("# Learned joint control vs the MPC — results (pre-registered)")
    w("")
    w("Protocol frozen in `PREREG_LEARNED_CONTROL.md`, pushed before the "
      "training run, the tuning sweep, or any matrix run. The learned arm is "
      "the RL analog of PolyForge's MPC: tabular Q-learning over the same "
      "≤60-candidate replicas×cache×tier lattice, rewarded on the same "
      "objective, with a shared tenant-agnostic policy trained offline "
      "(`baselines/train_learned.py`, seeds disjoint from this matrix) and "
      "deployed frozen-greedy. `learned_online` is the FIRM-style "
      "learn-during-the-run ablation. Rerun: `python analysis_learned.py`.")
    w("")

    n_by_system = runs.groupby("system").size().to_dict()
    w(f"Valid runs: {len(runs)} " + ", ".join(f"{k}={v}" for k, v in sorted(n_by_system.items())))
    w("")
    w("## Per-system summary (mean over the matrix cells)")
    w("")
    summary = runs.groupby("system")[["J", "total_cost_usd", "mean_violation",
                                      "mean_jain"]].mean()
    order = [s for s in ORDER if s in summary.index]
    pretty = summary.loc[order].reset_index()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")

    # --- LR-H1: non-inferiority of the MPC to the trained learned policy ---
    w("## LR-H1 (confirmatory, non-inferiority) — MPC vs offline-trained learned policy")
    w("")
    ni = non_inferiority(runs, "jcac_anchored", "learned_trained", "J", NI_MARGIN_FRAC)
    sup = paired(runs, "jcac_anchored", "learned_trained", "J")
    h1 = ni["p"] < 0.01
    # Pre-committed falsifier: the learned policy genuinely beats the MPC.
    falsified = (sup["mean_diff"] > 0 and sup["p"] < 0.01 and abs(sup["dz"]) >= 0.5)
    w(md_table(pd.DataFrame([
        {"reading": "LR-H1: J non-inferiority", "n": ni["n"],
         "mean J (MPC)": ni["mean_a"], "mean J (learned)": ni["mean_b"],
         "diff (MPC−learned)": ni["mean_diff"], "margin δ": ni["delta"],
         "p (one-sided)": ni["p"], "verdict": "PASS" if h1 else "FAIL"},
        {"reading": "two-sided superiority (reported alongside)", "n": sup["n"],
         "mean J (MPC)": sup["mean_a"], "mean J (learned)": sup["mean_b"],
         "diff (MPC−learned)": sup["mean_diff"], "margin δ": float("nan"),
         "p (one-sided)": sup["p"], "verdict": f"d_z={sup['dz']:.3g}"},
    ])))
    w("")
    w(f"95% bootstrap CI of the paired J difference (MPC − learned): "
      f"[{ni['ci_lo']:.4g}, {ni['ci_hi']:.4g}] — the citable unit at this n.")
    w("")
    superior = sup["mean_diff"] < 0 and sup["p"] < 0.01
    if falsified:
        w("**LR-H1: FAIL — pre-committed falsifier fired.** The offline-trained "
          "learned policy *beats* the hand-designed MPC on the composite "
          "objective (p<0.01, |d_z|≥0.5). Per §4 this headlines the thesis "
          "limitations: a learned policy discovers better joint coordination "
          "than the MPC, and learned control becomes the primary future "
          "direction. Published exactly as measured.")
    elif h1 and superior:
        w("**LR-H1: PASS — and the pre-declared two-sided reading is stronger "
          "than the gate.** Non-inferiority holds, but the MPC is not merely "
          "*as good as* the offline-trained learned policy: it **beats** it on "
          "the composite objective "
          f"(diff {sup['mean_diff']:.4g}, p={sup['p']:.3g}, d_z={sup['dz']:.3g}). "
          "The citable claim is therefore the strong one: **the hand-designed "
          "joint controller outperforms a well-trained model-free learner "
          "while paying zero training cost**, and it stays interpretable and "
          "carries the Props 1–2 guarantees the learned policy cannot offer. "
          "The non-inferiority framing was chosen before the data existed "
          "precisely because parity was the outcome we expected to have to "
          "defend; the measured result exceeded it.")
    elif h1:
        w("**LR-H1: PASS** — the MPC is non-inferior to the offline-trained "
          "learned policy within the frozen 5% margin. The headline reading: "
          "**the hand-designed joint controller matches a well-trained "
          "model-free learner at zero training cost**, while remaining "
          "interpretable and carrying the Props 1–2 guarantees the learned "
          "policy does not.")
    else:
        w("**LR-H1: FAIL (non-inferiority not established)** — the MPC's J "
          "exceeds the learned policy's by more than the frozen margin, or "
          "the test is underpowered. Reported as measured; see the two-sided "
          "reading and CI above for the direction and magnitude.")
    w("")
    # Mechanism: where does the learned policy's J actually go?
    c = paired(runs, "jcac_anchored", "learned_trained", "total_cost_usd")
    v = paired(runs, "jcac_anchored", "learned_trained", "mean_violation")
    if c["mean_diff"] < 0 and v["mean_diff"] > 0:
        w(f"**Mechanism (descriptive).** The learned policy is *not* worse "
          f"everywhere: it attains slightly **lower violation** than the MPC "
          f"({v['mean_b']:.4g} vs {v['mean_a']:.4g}, diff {v['mean_diff']:+.4g}, "
          f"p={v['p']:.3g}) — but buys that attainment with "
          f"**{c['mean_b'] / max(c['mean_a'], 1e-9):.2f}× the spend** "
          f"({c['mean_b']:.4g} vs {c['mean_a']:.4g} USD, p={c['p']:.3g}). That is "
          "the same attainment-for-spend trade the tuned reactive scalers make "
          "(RESULTS_V2/V3): the learner rediscovers *buy headroom*, not the "
          "cost-efficient joint posture. Finding the cheap configuration — not "
          "meeting the SLO — is what the joint optimizer contributes.")
        w("")

    # --- LR-H2: data efficiency ---
    w("## LR-H2 (confirmatory, data-efficiency) — trained vs online learned")
    w("")
    h2r = paired(runs, "learned_trained", "learned_online", "J")
    h2 = h2r["mean_diff"] < 0 and h2r["p"] < 0.01
    w(md_table(pd.DataFrame([
        {"reading": "LR-H2: J (trained − online)", "n": h2r["n"],
         "mean J (trained)": h2r["mean_a"], "mean J (online)": h2r["mean_b"],
         "diff": h2r["mean_diff"], "p": h2r["p"], "d_z": h2r["dz"],
         "verdict": "PASS" if h2 else "FAIL"},
    ])))
    w("")
    if h2:
        w("**LR-H2: PASS** — the parity in LR-H1 is *bought* by the committed "
          "offline training budget. Model-free learning of the joint policy "
          "within a single deployment episode is materially worse; the MPC "
          "pays neither cost.")
    else:
        w("**LR-H2: FAIL** — the offline training budget did not significantly "
          "improve on learn-during-the-run. Reported as measured (§4's "
          "symmetric disclosure).")
    w("")

    # --- LR-D1: descriptive ---
    w("## LR-D1 (descriptive, no gate)")
    w("")
    w("Paired readings vs the MPC (negative = MPC better):")
    w("")
    rows = []
    for sysname in ("learned_trained", "learned_online", "hpa"):
        if sysname not in set(runs.system):
            continue
        for metric, name in (("J", "J"), ("total_cost_usd", "cost"),
                             ("mean_violation", "violation"), ("mean_jain", "Jain")):
            r = paired(runs, "jcac_anchored", sysname, metric)
            rows.append({"vs": LABELS[sysname], "metric": name,
                         "MPC mean": r["mean_a"], "other mean": r["mean_b"],
                         "diff (MPC−other)": r["mean_diff"], "p": r["p"],
                         "d_z": r["dz"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("Per workload class (J diff, MPC − trained-learned; negative = MPC better):")
    w("")
    rows = []
    for wl in sorted(runs.workload.unique()):
        r = paired(runs[runs.workload == wl], "jcac_anchored", "learned_trained", "J")
        rows.append({"class": wl, "MPC J": r["mean_a"], "learned J": r["mean_b"],
                     "J diff": r["mean_diff"], "p": r["p"], "d_z": r["dz"],
                     "n": r["n"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("## Notes")
    w("")
    w("- **Erratum (disclosed, protocol unchanged).** `PREREG_LEARNED_CONTROL` "
      "§3 labels the matrix \"1,500 runs\"; the frozen design it specifies — 4 "
      "systems × 5 workload classes × 4 tenant mixes × 3 cluster sizes × 5 "
      "reps — is **1,200** runs, and 1,200 is what executed and validated "
      "(all green). The stated total was an arithmetic slip in the "
      "pre-registration text; the design, the systems, the cells and the "
      "confirmatory unit (300 matched pairs, as §3 itself states) are exactly "
      "as frozen. Recorded here rather than corrected upstream: frozen "
      "protocols are not edited after the fact.")
    w("- The learned arm is trained on seeds disjoint from every cell scored "
      "here (train/val/test split, `PREREG_LEARNED_CONTROL` §2); the deployed "
      "policy is frozen (`train=False`, ε=0), so the committed Q-table alone "
      "determines its behavior and the runs replay from it.")
    w("- Fairness is not in the learned reward (cross-tenant, as with FIRM); "
      "the Jain column is measured, not optimized by that arm.")
    w("- Same substrate rules as every matrix campaign: sim-backend decision "
      "quality, blocked-factorial matched cells, never mixed with replay "
      "tables (ground rules 3–4, 6).")
    w("- Stopping rule §5 honored: one training run, one tuning sweep, one "
      "matrix execution, one analysis pass. SLO confirmatory attempts remain "
      "closed (v3 rule): LR-H1/H2 are J gates only.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
