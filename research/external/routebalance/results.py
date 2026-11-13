"""RESULTS.md from data/*.json (called by `python -I analyse.py report`)."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analyse as A
import fleet as F
import rb_model as M

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
THEORY = REPO / "research" / "theory"
OUR_REGIMES = ["width/published/narrow", "width/published/wide", "width/coupled/narrow", "width/coupled/wide",
               "width/live/narrow", "width/live/wide", "jointness/main", "level2/main",
               "dose/k1/w1", "dose/k1/w16", "dose/k2/w16"]


def _load(name: str) -> dict:
    return json.loads((A.DATA / name).read_text(encoding="utf-8"))


def _pct(x, d=1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.{d}f}%"


def _f(x, d=3) -> str:
    return "n/a" if x is None else f"{x:.{d}f}"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def cells_frame(runs: dict, theirs: dict) -> pd.DataFrame:
    rows = []
    for preset, d in runs.items():
        for rate, c in d["rates"].items():
            o = {p: c[p]["outcomes"] for p in F.POLICIES}
            a = c["fused"]["decisions"]
            tf, td = theirs.get(f"fused|{preset}|{rate}"), theirs.get(f"decoupled|{preset}|{rate}")
            rows.append({
                "preset": preset, "rate": int(rate),
                "their_e2e": 1 - tf["e2e_mean_s"] / td["e2e_mean_s"],
                "their_cost": 1 - tf["cost_per_million_usd"] / td["cost_per_million_usd"],
                "model_e2e": 1 - o["fused"]["e2e_mean_s"] / o["decoupled"]["e2e_mean_s"],
                "model_cost": 1 - o["fused"]["cost_per_million_usd"] / o["decoupled"]["cost_per_million_usd"],
                "seq_e2e": o["sequential"]["e2e_mean_s"] / o["fused"]["e2e_mean_s"] - 1,
                "seq_cost": o["sequential"]["cost_per_million_usd"] / o["fused"]["cost_per_million_usd"] - 1,
                **{k: a[k] for k in ("index_share_mean", "index_mean", "quantile_index_mean", "index_positive_share",
                                     "loss_perknob_share_mean", "loss_sequential_share_mean", "gain_fused_share_mean",
                                     "model_differs_share", "gain_left_to_sequential", "gain_left_to_perknob",
                                     "theorem1_checked", "theorem1_violations", "index_share_of_spread_mean",
                                     "costly_perknob_share")}})
    return pd.DataFrame(rows)


def our_run_gains() -> dict:
    """Run-level coordination gain as a share of the joint arm's J, from our committed pilots."""
    out = {}
    dr = pd.read_csv(THEORY / "data" / "dose_response_runs.csv.gz")
    piv = dr.pivot_table(index=["kappa", "width", "workload", "tenant_mix"], columns="search", values="J")
    for name, (k, w) in {"dose/k1/w1": (1.0, 1), "dose/k1/w16": (1.0, 16), "dose/k2/w16": (2.0, 16)}.items():
        s = piv.loc[(k, w)]
        out[name] = float(((s["decoupled"] - s["joint"]) / s["joint"]).mean())
    sys.path.insert(0, str(THEORY))
    for p in (REPO / "research" / "jcac_sim", REPO / "eval"):
        sys.path.insert(0, str(p))
    import index_pilot as ip
    for regime in OUR_REGIMES[:8]:
        experiment, joint, _, kind = ip.REGIMES[regime]
        ex = ip._export(experiment)
        ex = ex[(ex.rep == 0) & (ex.system == joint)].assign(OBJ=lambda d: ip.objective(d, kind)).set_index(ip.DESIGN).OBJ
        gain = ip.run_level_gain(regime)
        out[regime] = float((gain / ex.reindex(gain.index)).mean())
    return out


def _spearman(df, a, b) -> float:
    return float(df[a].rank().corr(df[b].rank()))


def section_calibration(cal: dict) -> list[str]:
    s = cal["surrogate"]
    L = ["## 1. Calibration (fitted) and round timing (not fitted)", "",
         f"Estimate surrogate: s = {s['s']}, s_len = {s['s_len']} (fitted to RB-decoupled's tier mix at the eight "
         f"presets, which is a function of the estimates alone), a = {s['a']} (fitted to the round's quality range). "
         "Tier order 14B / 3B / 72B / 7B.", "",
         "| preset | RB-decoupled mix, their runs | model |", "|---|---|---|"]
    for pr, v in cal["mix"].items():
        L.append(f"| {pr} | {' / '.join(f'{x:.3f}' for x in v['theirs'])} | {' / '.join(f'{x:.3f}' for x in v['model'])} |")
    L += ["", "| rate (req/s) | mean round size, theirs | model (not fitted) | round quality range, theirs | model |",
          "|---:|---:|---:|---:|---:|"]
    for r, v in cal["rounds"].items():
        L.append(f"| {r} | {v['theirs_size']:.2f} | {v['model_size']:.2f} | {v['theirs_range']:.3f} | {v['model_range']:.3f} |")
    L += ["", f"Not fitted: the share of prompts the 72B is estimated to answer best is {cal['big_best_share']['model']:.3f} "
          f"(their logs {cal['big_best_share']['theirs']:.3f}); its mean lead there {cal['big_lead']['model']:.4f} "
          f"({cal['big_lead']['theirs']:.4f}).", ""]
    return L


def section_validation(runs: dict, theirs: dict, df: pd.DataFrame) -> list[str]:
    L = ["## 2. Does the generator reproduce their runs? (nothing below is fitted)", "",
         "80/10/10, seed 1. E2E mean (s) and cost (USD per million requests); theirs from data/qwen_report.json. "
         "The model's absolute E2E and cost run low (the chat template's prompt tokens, network and streaming are not "
         "modelled); the comparison is the relative change.", "",
         "| rate | E2E fused theirs / model | E2E RB-decoupled theirs / model | E2E per-request norm. theirs / model "
         "| cost fused theirs / model | cost RB-dec. theirs / model | 72B share fused theirs / model | E2E sequential (model) |",
         "|---:|---|---|---|---|---|---|---:|"]
    for rate, c in runs["80/10/10"]["rates"].items():
        o = {p: c[p]["outcomes"] for p in F.POLICIES}
        t = {p: theirs[f"{p}|80/10/10|{rate}"] for p in ("fused", "decoupled", "reqnorm")}
        L.append(f"| {rate} | {t['fused']['e2e_mean_s']:.2f} / {o['fused']['e2e_mean_s']:.2f} "
                 f"| {t['decoupled']['e2e_mean_s']:.2f} / {o['decoupled']['e2e_mean_s']:.2f} "
                 f"| {t['reqnorm']['e2e_mean_s']:.2f} / {o['reqnorm']['e2e_mean_s']:.2f} "
                 f"| {t['fused']['cost_per_million_usd']:.1f} / {o['fused']['cost_per_million_usd']:.1f} "
                 f"| {t['decoupled']['cost_per_million_usd']:.1f} / {o['decoupled']['cost_per_million_usd']:.1f} "
                 f"| {t['fused']['mix'][2]:.3f} / {o['fused']['mix'][2]:.3f} | {o['sequential']['e2e_mean_s']:.2f} |")
    L += ["", f"Across all 48 preset x rate cells, RouteBalance's reduction against RB-decoupled, model against their runs: "
          f"E2E Pearson {df.model_e2e.corr(df.their_e2e):.2f}, Spearman {_spearman(df, 'model_e2e', 'their_e2e'):.2f}; "
          f"cost Pearson {df.model_cost.corr(df.their_cost):.2f}, Spearman {_spearman(df, 'model_cost', 'their_cost'):.2f}. "
          f"Mean reduction, theirs / model: E2E {_pct(df.their_e2e.mean())} / {_pct(df.model_e2e.mean())}, cost "
          f"{_pct(df.their_cost.mean())} / {_pct(df.model_cost.mean())}.", "",
          f"The sequential design on the shared objective (router scores each model at the balancer's pick, then the "
          f"balancer picks within the router's model, both on RouteBalance's round-normalised objective; not in the paper) "
          f"differs from fused RouteBalance by at most {_pct(df.seq_e2e.abs().max())} in E2E and {_pct(df.seq_cost.abs().max())} "
          f"in cost over the 48 cells (mean {_pct(df.seq_e2e.mean())} and {_pct(df.seq_cost.mean())}).", ""]
    return L


def section_index(df: pd.DataFrame) -> list[str]:
    cols = ["index_share_mean", "index_positive_share", "loss_perknob_share_mean", "loss_sequential_share_mean",
            "gain_fused_share_mean", "model_differs_share", "gain_left_to_sequential", "their_e2e", "their_cost"]
    L = ["## 3. The index on their decision (states of the fused runs, instances ranked by the balancer)", "",
         "Per decision, as shares of phi at the fused optimum (mean over decisions, then over the six rates). "
         "Gain = phi(RB-decoupled's choice) - phi(fused). Left to sequential = sequential loss / gain.", "",
         "| preset | index | index > 0 | per-knob loss | sequential loss | gain over RB-decoupled | model differs "
         "| gain left to sequential | their E2E cut | their cost cut |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    g = df.groupby("preset")[cols].mean()
    for pr in M.PRESETS:
        r = g.loc[pr]
        L.append(f"| {pr} | {_pct(r.index_share_mean)} | {_pct(r.index_positive_share, 0)} | {_pct(r.loss_perknob_share_mean, 2)} "
                 f"| {_pct(r.loss_sequential_share_mean, 2)} | {_pct(r.gain_fused_share_mean)} | {_pct(r.model_differs_share, 0)} "
                 f"| {_pct(r.gain_left_to_sequential, 0)} | {_pct(r.their_e2e)} | {_pct(r.their_cost)} |")
    L += ["", f"Theorem 1 checked on {int(df.theorem1_checked.sum()):,} decisions: "
          f"{int(df.theorem1_violations.sum())} violations.", ""]
    sub = df[df.preset == "80/10/10"].sort_values("rate")
    L += ["80/10/10 by rate:", "", "| rate | index | index > 0 | per-knob loss | sequential loss | gain over RB-decoupled "
          "| model differs | their E2E cut |", "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in sub.iterrows():
        L.append(f"| {r.rate} | {_pct(r.index_share_mean)} | {_pct(r.index_positive_share, 0)} | {_pct(r.loss_perknob_share_mean, 2)} "
                 f"| {_pct(r.loss_sequential_share_mean, 2)} | {_pct(r.gain_fused_share_mean)} | {_pct(r.model_differs_share, 0)} "
                 f"| {_pct(r.their_e2e)} |")
    return L + [""]


def section_test(df: pd.DataFrame) -> list[str]:
    cands = [("index (share of phi*)", "index_share_mean"), ("index (objective units)", "index_mean"),
             ("quantile index", "quantile_index_mean"), ("index share of the decision's spread", "index_share_of_spread_mean"),
             ("per-knob loss (share)", "loss_perknob_share_mean"), ("sequential loss (share)", "loss_sequential_share_mean"),
             ("costly per-knob disagreements", "costly_perknob_share"),
             ("per-decision gain over RB-decoupled (share)", "gain_fused_share_mean"),
             ("model's own run-level E2E cut", "model_e2e"), ("model's own run-level cost cut", "model_cost")]
    L = ["## 4. Out-of-sample test: does the index track where their fusion paid?", "",
         "Spearman rank correlation over the 48 preset x rate cells between each per-decision quantity (model, fused "
         "states) and RouteBalance's measured reduction against RB-decoupled (their seed-1 runs).", "",
         "| candidate | vs their E2E cut | vs their cost cut |", "|---|---:|---:|"]
    for name, col in cands:
        L.append(f"| {name} | {_spearman(df, col, 'their_e2e'):+.2f} | {_spearman(df, col, 'their_cost'):+.2f} |")
    return L + [""]


def section_scale(df: pd.DataFrame, ours: dict, gains: dict) -> list[str]:
    L = ["## 5. Common scale: their decision against our slow knobs", "",
         "Per-decision index and one-step (per-knob) loss as shares of phi at the joint optimum; ours from short re-runs "
         "(our_scale.py: 4 design points x 24 intervals, 2 for the width-16 dose regimes), theirs from section 3. "
         "Run gain: ours = decoupled minus joint J over joint J (committed pilots); theirs = measured E2E and cost cuts "
         "against RB-decoupled (not the same objective).", "",
         "| system, regime | index share, mean | median | index share of spread | per-knob loss share | run gain |",
         "|---|---:|---:|---:|---:|---|"]
    for reg in OUR_REGIMES:
        v = ours.get(reg)
        if v is None:
            continue
        L.append(f"| ours, {reg} | {_pct(v['index_share_of_phi_joint_mean'])} | {_pct(v['index_share_of_phi_joint_median'])} "
                 f"| {_pct(v['index_share_of_spread_mean'])} | {_pct(v['loss_share_of_phi_joint_mean'], 2)} "
                 f"| {_pct(gains.get(reg), 1)} of J |")
    g = df.groupby("preset")
    for pr in M.PRESETS:
        s = g.get_group(pr)
        L.append(f"| RouteBalance, {pr} | {_pct(s.index_share_mean.mean())} | n/a | {_pct(s.index_share_of_spread_mean.mean())} "
                 f"| {_pct(s.loss_perknob_share_mean.mean(), 2)} | E2E {_pct(s.their_e2e.mean())}, cost {_pct(s.their_cost.mean())} |")
    return L + [""]


def section_robustness(runs: dict, syn: dict, df: pd.DataFrame) -> list[str]:
    L = ["## 6. Robustness", "", "| variant (80/10/10, mean over rates) | index share | per-knob loss share "
         "| sequential loss share | gain share | model differs |", "|---|---:|---:|---:|---:|---:|"]
    for label, key, pol in (("fused states, instances ranked by the balancer (main)", "decisions", "fused"),
                            ("fused states, instances ranked by latency", "decisions_latency_order", "fused"),
                            ("per-request objective (their ablation's states)", "decisions", "reqnorm")):
        rs = [c[pol][key] for c in runs["80/10/10"]["rates"].values() if key in c[pol]]
        m = {k: statistics.fmean(r[k] for r in rs) for k in ("index_share_mean", "loss_perknob_share_mean",
                                                              "loss_sequential_share_mean", "gain_fused_share_mean",
                                                              "model_differs_share")}
        L.append(f"| {label} | {_pct(m['index_share_mean'])} | {_pct(m['loss_perknob_share_mean'], 2)} "
                 f"| {_pct(m['loss_sequential_share_mean'], 2)} | {_pct(m['gain_fused_share_mean'])} | {_pct(m['model_differs_share'], 0)} |")
    L += ["", f"Stated synthetic ranges (model.SYNTH_RANGES, {syn['n']} decisions per preset):", "",
          "| preset | index share | index > 0 | per-knob loss share | gain share | Theorem 1 violations |", "|---|---:|---:|---:|---:|---:|"]
    for pr, a in syn["presets"].items():
        L.append(f"| {pr} | {_pct(a['index_share_mean'])} | {_pct(a['index_positive_share'], 0)} "
                 f"| {_pct(a['loss_perknob_share_mean'], 2)} | {_pct(a['gain_fused_share_mean'])} | {a['theorem1_violations']} |")
    probes = [c["fused"]["probe"] for d in runs.values() for c in d["rates"].values()]
    L += ["", f"tools/index-probe, log mode, on every 7th fused decision of every run ({sum(p['decisions'] for p in probes):,} "
          f"decisions): Theorem 1 checked {sum(p['theorem1_checked'] for p in probes):,}, violated "
          f"{sum(p['theorem1_violations'] for p in probes)}; largest difference from this code's index "
          f"{max(p['max_index_difference'] for p in probes):.1e}.", ""]
    return L


def write_results() -> Path:
    cal, syn = _load("calibration.json"), _load("synthetic.json")
    runs = {M.PRESETS and _load(f"runs_{A._slug(pr)}.json")["preset"]: _load(f"runs_{A._slug(pr)}.json") for pr in M.PRESETS}
    theirs = A.their_cells()
    df = cells_frame(runs, theirs)
    ours = _load("our_scale.json")
    gains = our_run_gains()
    labels = F.UPSTREAM / "data" / "labels" / "qwen_eval.jsonl"
    head = ["# RouteBalance through the interaction index: generated results (WP15)", "",
            "Generated by `analyse.py report` from data/*.json; EXPLORATORY. Paper: arXiv 2606.17949v2. Their code and "
            "data: github.com/AKafakA/route-balance, branch submission at ac49feb (read, never run; "
            f"qwen_eval.jsonl sha256 {_sha(labels)}...). Objective: the penalty phi = 1 - S of model.py. Every "
            "simplification is listed in REPORT.md.", ""]
    body = (section_calibration(cal) + section_validation(runs, theirs, df) + section_index(df) + section_test(df)
            + section_scale(df, ours, gains) + section_robustness(runs, syn, df))
    out = HERE / "RESULTS.md"
    out.write_text("\n".join(head + body), encoding="utf-8")
    _dump_cells(df)
    return out


def _dump_cells(df: pd.DataFrame) -> None:
    A._dump(A.DATA / "cells.json", df.to_dict(orient="records"))
