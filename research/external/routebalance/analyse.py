"""WP15: the interaction index of RouteBalance's fused decision, against its decoupled design.

Every step below fits the 2-minute CPU limit on one process; run them in this order.

    python -I analyse.py calibrate               # fits the estimate surrogate to their released runs
    python -I analyse.py run --preset 80/10/10   # one preset, six rates -> data/runs_<preset>.json
    python -I analyse.py synthetic               # the stated synthetic ranges -> data/synthetic.json
    python -I analyse.py report                  # RESULTS.md from data/*.json and our pilots

calibrate and run read the authors' released data from upstream/ (see README.md); synthetic does not.
Objective units: the penalty 1 - S of model.py. "Share" = per-decision ratio to phi at the joint
optimum, averaged over decisions, unless stated otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (HERE, REPO / "tools" / "index-probe"):
    if str(p) not in sys.path:      # python -I: this directory and the probe only, never upstream/
        sys.path.insert(0, str(p))

import fleet as F  # noqa: E402
import rb_model as M  # noqa: E402

DATA = HERE / "data"
PACK = F.UPSTREAM / "paper" / "routebalance-paper-data-v1.5" / "data"
RATES = (12, 18, 24, 30, 36, 42)
CONFIG_TAG = {"uniform": "uniform", "50/30/20": "q50l30c20", "60/20/20": "q60l20c20", "80/10/10": "q80l10c10",
              "90/5/5": "q90l05c05", "5/25/70": "q05l25c70", "22/18/60": "q22l18c60", "25/5/70": "q25l05c70"}
EPS = 1e-9


def _slug(preset: str) -> str:
    return preset.replace("/", "-")


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str), encoding="utf-8")


# ---------------------------------------------------------------- their released runs (data, read only)

def their_cells() -> dict:
    """Per (system, preset, rate) at seed 1: outcomes from data/qwen_report.json."""
    rep = json.loads((PACK / "qwen_report.json").read_text(encoding="utf-8"))["cells"]
    out = {}
    for system, prefix in (("fused", "rb-static"), ("decoupled", "rb-static-dec"), ("reqnorm", "rb-static-reqnorm")):
        for preset, tag in CONFIG_TAG.items():
            for rate in RATES:
                c = rep.get(f"qwen.{prefix}.{tag}__l{rate}__s1")
                if c is None:
                    continue
                s = c["summary"]
                out[f"{system}|{preset}|{rate}"] = {
                    "e2e_mean_s": s["e2e_ms"]["mean"] / 1000.0, "cost_per_million_usd": s["cost_total_per_1k"] * 1000,
                    "quality": s["quality"], "before_dispatch_ms": s["off_instance_ms"]["mean"],
                    "mix": [c["model_mix"].get(m, 0.0) for m in M.MODELS]}
    return out


def their_rounds() -> dict:
    rr = json.loads((PACK / "round_range.json").read_text(encoding="utf-8"))
    return {rate: {"mean_round_size": v["mean_round_size"], "round_range": v["range_mean_rounds"],
                   "big_best_share": v["big_best_share"], "big_lead": v["big_lead_raw_mean_when_best"],
                   "repeat_share": None}
            for rate in RATES for k, v in rr.items() if k.endswith(f"__l{rate}__s1")}


# ---------------------------------------------------------------- calibration of the estimate surrogate

def decoupled_mix(q: np.ndarray, length: np.ndarray, p: np.ndarray, w: tuple) -> np.ndarray:
    """model.decoupled_model over every prompt at once (test_model.py checks they agree)."""
    w_q, w_l, w_c = w
    tiers = M.TIERS
    qn = (q - q.min(1, keepdims=True)) / (q.max(1, keepdims=True) - q.min(1, keepdims=True) + M.EPS)
    c = p[:, None] * np.array([t.cost_in for t in tiers]) + length * np.array([t.cost_out for t in tiers])
    lat = (np.maximum(1, np.floor(length)) * np.array([t.tpot_s for t in tiers])
           + np.array([t.prefill_a_s for t in tiers]) + p[:, None] * np.array([t.prefill_b_s for t in tiers]))
    s = w_q * qn + w_c * (1.0 - c / (c.max(1, keepdims=True) + M.EPS)) + w_l * (1.0 - lat / (lat.max(1, keepdims=True) + M.EPS))
    pen = 1.0 - s
    return np.bincount(pen.argmin(1), minlength=len(tiers)) / len(pen)


def calibrate() -> dict:
    lab = F.load_labels()
    cells = their_cells()
    target = {pr: np.array(cells[f"decoupled|{pr}|12"]["mix"]) for pr in M.PRESETS}
    p = lab.prompt_tokens.astype(float)
    best = None
    for s in np.round(np.arange(0.0, 0.41, 0.01), 2):
        for s_len in np.round(np.arange(0.0, 1.01, 0.05), 2):
            q, length = F.Surrogate(s, s_len, s).estimates(lab)
            err = sum(float(((decoupled_mix(q, length, p, M.PRESETS[pr]) - target[pr]) ** 2).sum()) for pr in M.PRESETS)
            if best is None or err < best[0]:
                best = (err, float(s), float(s_len))
    _, s, s_len = best
    theirs = their_rounds()
    parts = {r: F.round_partition(F.arrival_times(len(lab.ids), r, 1), r) for r in RATES}
    order = F.send_order(len(lab.ids), 1)
    fit_a = None
    for a in np.round(np.arange(0.0, 1.01, 0.02), 2):
        q, _ = F.Surrogate(s, s_len, a).estimates(lab)
        qs = q[order]
        hi, lo = qs.max(1), qs.min(1)
        ranges = {r: float(np.mean(np.maximum.reduceat(hi, [f for f, _, _ in pt]) - np.minimum.reduceat(lo, [f for f, _, _ in pt])))
                  for r, pt in parts.items()}
        err = sum((ranges[r] - theirs[r]["round_range"]) ** 2 for r in RATES)
        if fit_a is None or err < fit_a[0]:
            fit_a = (err, float(a), ranges)
    sur = F.Surrogate(s, s_len, fit_a[1])
    q, length = sur.estimates(lab)
    big = M.MODELS.index("Qwen/Qwen2.5-72B")
    others = np.delete(q, big, 1).max(1)
    lead = q[:, big] - others
    out = {"surrogate": {"s": s, "s_len": s_len, "a": fit_a[1]}, "mix_sse": best[0], "range_sse": fit_a[0],
           "mix": {pr: {"theirs": target[pr].tolist(), "model": decoupled_mix(q, length, p, M.PRESETS[pr]).tolist()}
                   for pr in M.PRESETS},
           "rounds": {r: {"theirs_size": theirs[r]["mean_round_size"], "model_size": float(np.mean([e - f for f, e, _ in parts[r]])),
                          "theirs_range": theirs[r]["round_range"], "model_range": fit_a[2][r]} for r in RATES},
           "big_best_share": {"theirs": theirs[12]["big_best_share"], "model": float((lead > 0).mean())},
           "big_lead": {"theirs": theirs[12]["big_lead"], "model": float(lead[lead > 0].mean())}}
    _dump(DATA / "calibration.json", out)
    return out


def load_surrogate() -> F.Surrogate:
    c = json.loads((DATA / "calibration.json").read_text(encoding="utf-8"))["surrogate"]
    return F.Surrogate(c["s"], c["s_len"], c["a"])


# ---------------------------------------------------------------- per-decision aggregates

def _mean(v) -> float | None:
    v = [x for x in v if x is not None]
    return statistics.fmean(v) if v else None


def aggregate(rows: list[dict]) -> dict:
    applies = [r for r in rows if r["theorem1_applies"]]
    viol = sum(1 for r in applies if not (-EPS <= r["loss_perknob"] <= r["bound"] + EPS and r["bound"] <= r["index"] + EPS))
    pj = [r["phi_joint"] for r in rows]
    idx = sorted(r["index"] for r in rows)
    gain = _mean(r["gain_fused"] for r in rows)
    lpk = _mean(r["loss_perknob"] for r in applies)
    lseq = _mean(r["loss_sequential"] for r in rows)
    return {
        "decisions": len(rows), "phi_joint_mean": _mean(pj), "spread_mean": _mean(r["spread"] for r in rows),
        "index_mean": _mean(idx), "index_median": statistics.median(idx), "index_p90": idx[int(0.9 * (len(idx) - 1))],
        "index_positive_share": sum(1 for x in idx if x > EPS) / len(idx),
        "quantile_index_mean": _mean(r["quantile_index"] for r in rows),
        "index_share_mean": _mean(r["index"] / r["phi_joint"] for r in rows),
        "index_share_of_spread_mean": _mean(r["index"] / r["spread"] for r in rows if r["spread"] > EPS),
        "loss_perknob_mean": lpk, "loss_perknob_share_mean": _mean(r["loss_perknob"] / r["phi_joint"] for r in applies),
        "costly_perknob_share": sum(1 for r in applies if r["loss_perknob"] > EPS) / max(1, len(applies)),
        "needs_repair_share": sum(r["needs_repair"] for r in rows) / len(rows),
        "loss_sequential_mean": lseq, "loss_sequential_share_mean": _mean(r["loss_sequential"] / r["phi_joint"] for r in rows),
        "loss_iterated_mean": _mean(r["loss_iterated"] for r in rows),
        "gain_fused_mean": gain, "gain_fused_share_mean": _mean(r["gain_fused"] / r["phi_joint"] for r in rows),
        "anchor_differs_share": sum(r["disagree_anchor"] for r in rows) / len(rows),
        "model_differs_share": sum(r["model_differs"] for r in rows) / len(rows),
        "gain_left_to_sequential": (lseq / gain) if gain and gain > EPS else None,
        "gain_left_to_perknob": (lpk / gain) if gain and gain > EPS and lpk is not None else None,
        "theorem1_checked": len(applies), "theorem1_violations": viol,
        "queued_share": sum(1 for r in rows if r["queued_instances"] > 0) / len(rows),
    }


def probe_check(decisions: list, path: Path) -> dict:
    """The same decisions through tools/index-probe's own log mode (its code, its Theorem 1 check)."""
    import index_probe
    with path.open("w", encoding="utf-8") as f:
        for n, d in enumerate(decisions):
            f.write(json.dumps(d.probe_record(f"d{n}")) + "\n")
    rows, summary = index_probe.run_log(path)
    ours = [M.analyse(d)["index"] for d in decisions]
    worst = max(abs(a - (b["index"] or 0.0)) for a, b in zip(ours, rows))
    return {**probe_fields(rows, summary), "max_index_difference": worst}


def probe_fields(rows: list[dict], summary: dict) -> dict:
    """index-probe's account of the logged decisions: Theorem 1 checked and violated, every decision
    outside it by reason, and those of them that lose more than their index (the largest excess)."""
    import index_probe
    above = [r for r in rows if r["outside_theorem1"] and r["loss"] is not None and r["index"] is not None
             and r["loss"] > r["index"] + index_probe.EPS]
    top = max(above, key=lambda r: r["loss"] - r["index"], default=None)
    return {"decisions": summary["decisions"], "theorem1_checked": summary["theorem1_checked"],
            "theorem1_violations": summary["theorem1_violations"], "index_mean": summary["index"]["mean"],
            "outside_theorem1": summary["outside_theorem1"], "outside_loss_above_index": len(above),
            "outside_largest_excess": None if top is None else {"loss": top["loss"], "index": top["index"]}}


# ---------------------------------------------------------------- simulated runs

def run(preset: str, rates: list[int], latency_order: bool) -> dict:
    lab, sur = F.load_labels(), load_surrogate()
    w = M.PRESETS[preset]
    out = {"preset": preset, "surrogate": vars(sur), "rates": {}}
    for rate in rates:
        cell, t0 = {}, time.time()
        for policy in F.POLICIES:
            log = policy in ("fused", "reqnorm")
            res = F.simulate(F.SimConfig(float(rate), w, policy, log_decisions=log), lab, sur)
            cell[policy] = {"outcomes": res.outcomes}
            if log:
                cell[policy]["decisions"] = aggregate([M.analyse(d) for d in res.decisions])
            if policy == "fused":
                cell["fused"]["probe"] = probe_check(res.decisions[::7], DATA / f"probe_{_slug(preset)}_l{rate}.jsonl")
                if latency_order:
                    alt = [M.Decision(d.request, d.states, d.scales, d.weights, "latency") for d in res.decisions]
                    cell["fused"]["decisions_latency_order"] = aggregate([M.analyse(d) for d in alt])
        out["rates"][rate] = cell
        print(f"{preset} {rate} req/s: {time.time() - t0:.1f}s", flush=True)
    _dump(DATA / f"runs_{_slug(preset)}.json", out)
    return out


def synthetic(n: int) -> dict:
    out = {"ranges": M.SYNTH_RANGES, "n": n, "presets": {}}
    for preset, w in M.PRESETS.items():
        rows = [M.analyse(d) for d in M.synthetic_decisions(n, seed=15, weights=w)]
        out["presets"][preset] = aggregate(rows)
    _dump(DATA / "synthetic.json", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("calibrate")
    r = sub.add_parser("run")
    r.add_argument("--preset", required=True, choices=list(M.PRESETS))
    r.add_argument("--rates", default=",".join(map(str, RATES)))
    r.add_argument("--latency-order", action="store_true", help="also analyse with instances ranked by latency")
    s = sub.add_parser("synthetic")
    s.add_argument("--n", type=int, default=3000)
    sub.add_parser("report")
    args = ap.parse_args()
    t0 = time.time()
    if args.cmd == "calibrate":
        c = calibrate()
        print(json.dumps({k: c[k] for k in ("surrogate", "mix_sse", "range_sse", "big_best_share", "big_lead")}, indent=1))
    elif args.cmd == "run":
        run(args.preset, [int(x) for x in args.rates.split(",")], args.latency_order)
    elif args.cmd == "synthetic":
        synthetic(args.n)
    else:
        from results import write_results
        write_results()
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
