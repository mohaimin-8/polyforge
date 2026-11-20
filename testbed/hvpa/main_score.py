"""WP17: the scorer of the main live runs (H8 of plan v2's Part D), as main_design.json fixes it.

Reads the committed plan (`main_runs.py plan`) and the runs' records (`main_runs.py run`: one valid
record per arm and schedule at pilot.record_path, invalid attempts kept beside it), and scores:

  contrasts  per live regime, A4 (joint) against A3 (per-knob) and against A1 (HPA with VPA, the
             same-metric autoscaler pair): d_k = J(arm) - J(A4) on every schedule where both arms
             have a valid record (J = cost + 1e-4 x late requests, pilot.j_live). The plan names each
             contrast's test: superiority, a one-sided paired t-test that the mean of d is above
             zero; or equivalence, two one-sided paired t-tests against +-margin x the comparator's
             mean J. Holm's correction runs over every contrast of the plan.
  H8 rank    Spearman's rho between the twin's mean index of the live regimes (predictions.json)
             and the regimes' observed gain against A3 (mean d over A3's mean J). With three
             regimes no permutation p-value can fall below 1/6, so this part is a criterion
             (rho >= rank_correlation_min), not a test.
  H8 beat    the A1 contrasts: joint control beats the autoscaler pair in a regime when that
             contrast is a superiority test supported after Holm.

A schedule without both records is missing data for that contrast; the count is reported. A design
still marked draft scores only with --allow-draft (a pipeline check, never a result). Writes
score.json and SCORE.md.

    python main_score.py --plan main_plan.json --runs ../out/hvpa/main --out ../out/hvpa/main_score
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "research" / "registration"))

import pilot  # noqa: E402
import wp14_score as stats  # noqa: E402  (the paired tests, Holm and Spearman WP14 freezes)

DESIGN = HERE / "main_design.json"
PREDICTIONS = REPO / "research" / "hvpa" / "predictions.json"
COMPARATORS = ("A3", "A1")


def load_design(path: Path = DESIGN, allow_draft: bool = False) -> dict:
    design = json.loads(path.read_text(encoding="utf-8"))
    if design.get("draft") and not allow_draft:
        raise SystemExit(f"{path.name} is still marked draft: a protocol fixes it and freezes it before any "
                         "main run is scored (--allow-draft checks the pipeline only)")
    return design


def record(runs: Path, width: int, sigma: float, k: int, arm: str) -> dict | None:
    """The arm's valid record of schedule k, or None when there is none. A record at the valid path
    that is not a measurement means the records were altered: refused."""
    path = pilot.record_path(runs, width, sigma, k, arm)
    if not path.exists():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    if pilot.problems(rec):
        raise SystemExit(f"{path} is kept as valid but is not a measurement: {pilot.problems(rec)}")
    return rec


def paired(runs: Path, regime: dict, arm: str) -> tuple[list[float], list[float], int]:
    """(d_k = J(arm) - J(A4), J(arm), schedules left out) over the regime's schedules, in k order."""
    diffs, comparator, lost = [], [], 0
    for sc in regime["schedules"]:
        joint = record(runs, regime["width"], regime["sigma"], sc["k"], "A4")
        other = record(runs, regime["width"], regime["sigma"], sc["k"], arm)
        if joint is None or other is None:
            lost += 1
            continue
        j_arm = pilot.j_live(other)
        diffs.append(j_arm - pilot.j_live(joint))
        comparator.append(j_arm)
    return diffs, comparator, lost


def contrast(runs: Path, regime: dict, arm: str, margin: float) -> dict:
    diffs, comparator, lost = paired(runs, regime, arm)
    d = np.asarray(diffs, float)
    mean_j = float(np.mean(comparator)) if comparator else math.nan
    test = regime["contrasts"][arm]["test"]
    if test == "superiority":
        out = stats.superiority(d)
    elif test == "equivalence":
        out = stats.tost(d, margin * mean_j if comparator else math.nan)
    else:
        raise SystemExit(f"unknown test {test!r}: superiority or equivalence")
    sd = float(np.std(d, ddof=1)) if len(d) > 1 else math.nan
    return {"width": regime["width"], "sigma": regime["sigma"], "arm": arm, "test": test, **out, "sigma_d": sd,
            "mean_J_arm": mean_j, "gain": out["mean_d"] / mean_j if comparator else math.nan, "left_out": lost}


def key(c: dict) -> str:
    return f"w{c['width']} s{c['sigma']} A4 vs {c['arm']}"


def score(plan: dict, runs: Path, design: dict, predictions: dict) -> dict:
    want = hashlib.sha256(json.dumps(predictions, sort_keys=True).encode()).hexdigest()
    if plan.get("predictions_sha256") != want:
        raise SystemExit("the plan was made from other twin predictions than predictions.json")
    margin, alpha = float(design["equivalence_margin"]), float(design["alpha"])
    cs = [contrast(runs, r, arm, margin) for r in plan["regimes"] for arm in COMPARATORS if arm in r["contrasts"]]
    family = [key(c) for c in cs]
    adjusted = stats.holm({key(c): c["p"] for c in cs}, family)
    for c in cs:
        c["p_holm"] = adjusted[key(c)]
        c["supported"] = bool(math.isfinite(c["p_holm"]) and c["p_holm"] < alpha)
    index = {(r["width"], r["sigma"]): r[design["H8_rank"]["predictor"]] for r in predictions["regimes"]}
    gains = {(c["width"], c["sigma"]): c["gain"] for c in cs if c["arm"] == design["H8_rank"]["gain_of"]}
    regs = [g for g in gains if g in index and math.isfinite(gains[g])]
    rho = stats.spearman([index[g] for g in regs], [gains[g] for g in regs]) if len(regs) > 1 else math.nan
    rank = {"predictor": design["H8_rank"]["predictor"], "gain_of": design["H8_rank"]["gain_of"], "regimes": len(regs),
            "rho": rho, "criterion": float(design["H8_rank"]["rank_correlation_min"]),
            "met": bool(math.isfinite(rho) and rho >= float(design["H8_rank"]["rank_correlation_min"]))}
    beat = [{"regime": f"w{c['width']} s{c['sigma']}", "supported": c["supported"] and c["test"] == "superiority"}
            for c in cs if c["arm"] == "A1"]
    return {"contrasts": cs, "family": family, "alpha": alpha, "H8_rank": rank, "H8_beat": beat}


def f(x, fmt: str = "+.4f") -> str:
    return "n/a" if x is None or not math.isfinite(x) else format(x, fmt)


def report(s: dict, meta: dict) -> str:
    L = ["# WP17 main live runs: scores", ""]
    if meta["draft"]:
        L += ["**DRAFT DESIGN: a pipeline check, not a result.**", ""]
    L += [f"Generated by `testbed/hvpa/main_score.py` from plan sha256 `{meta['plan_sha256']}` and design sha256 "
          f"`{meta['design_sha256']}`. d = J(comparator) - J(A4), J = cost + 1e-4 x late requests; alpha "
          f"{s['alpha']}, Holm over the {len(s['family'])} contrasts.", "",
          "| contrast | test | pairs | left out | mean d (intervals) | sigma_d | gain | p | Holm p | supported |",
          "|---|---|---:|---:|---|---:|---:|---:|---:|---|"]
    for c in s["contrasts"]:
        # an equivalence contrast holds at 0.05 exactly when its 90% interval lies inside +-margin
        test = f"equivalence, margin +-{f(c['margin'], '.4f')}" if c["test"] == "equivalence" else c["test"]
        L.append(f"| {key(c)} | {test} | {c['n']} | {c['left_out']} | {stats.intervals(c)} | "
                 f"{f(c['sigma_d'])} | {f(c['gain'], '+.1%')} | {f(c['p'], '.4f')} | "
                 f"{f(c['p_holm'], '.4f')} | {'yes' if c['supported'] else 'no'} |")
    r = s["H8_rank"]
    L += ["", f"H8, rank: Spearman rho between the twin's `{r['predictor']}` and the gain against {r['gain_of']} over "
          f"{r['regimes']} regimes: {f(r['rho'], '+.3f')} (criterion >= {r['criterion']}: "
          f"{'met' if r['met'] else 'not met'}; a criterion, not a test).",
          "H8, beat: joint control beats HPA with VPA in " +
          f"{sum(b['supported'] for b in s['H8_beat'])} of {len(s['H8_beat'])} regimes "
          f"({', '.join(b['regime'] + (' yes' if b['supported'] else ' no') for b in s['H8_beat'])})."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", type=Path, required=True, help="main_runs.py's committed plan")
    ap.add_argument("--runs", type=Path, required=True, help="the runs' records (main_runs.py run --out)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--design", type=Path, default=DESIGN)
    ap.add_argument("--predictions", type=Path, default=PREDICTIONS)
    ap.add_argument("--allow-draft", action="store_true", help="score with a draft design: a pipeline check only")
    args = ap.parse_args(argv)
    design = load_design(args.design, args.allow_draft)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    s = score(plan, args.runs, design, predictions)
    meta = {"draft": bool(design.get("draft")),
            "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
            "design_sha256": hashlib.sha256(args.design.read_bytes()).hexdigest()}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "score.json").write_text(json.dumps(stats.clean({**meta, **s}), indent=1, allow_nan=False) + "\n",
                                         encoding="utf-8")
    text = report(s, meta)
    (args.out / "SCORE.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
