"""WP17 live pilot: paired runs per regime and contrast, then the pairs needed (programme 2).

From research/hvpa/predictions.json (committed before any live run): the live
regimes (width, volatility) and the tuned HPA targets. Per regime, two
contrasts: joint (A4) against per-knob (A3) at the regime's width, and joint
against the HPA-on-CPU + VPA pair (A1, default VPA flags, the target tuned for
the regime's volatility). Per contrast, PAIRS pairs in ABBA order (plan WP12):
pair k runs the two arms back to back on the same schedule (shape k mod the
shapes, seed k), A then B for even k and B then A for odd k. Each run is
live.run; its J is cost + KAPPA x late requests (late = requests x (1 -
attainment)), the twin's objective. Then, per contrast (plan WP12):
  sigma_d   the standard deviation of the paired differences J_B - J_A4;
  n_sup     pairs for superiority at the twin's predicted gain,
            ((1.645 + 1.282) sigma_d / Delta)^2, Delta = predicted gain x
            the comparator's mean J;
  n_eq      pairs for equivalence within +-2 % of the comparator's mean J,
            ((1.645 + 1.645) sigma_d / eps)^2.
Resumable: a run whose record exists is not repeated. Writes PILOT.md.

    python pilot.py --predictions ../../research/hvpa/predictions.json \
        --calibration ../../research/hvpa/calibration.json --out ../out/hvpa/pilot
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "research" / "hvpa"))

PAIRS = 6
KAPPA = 1e-4          # the twin's (lattice.KAPPA)
EQUIVALENCE = 0.02
Z_ALPHA, Z_POWER = 1.645, 1.282
ATTEMPTS = 3          # an invalid run is repeated at most this often, then its pair is left out
MAX_POLL_GAP_S = 30.0  # three poll periods: a longer gap is the host stalling (WP14), not the arm


def order(pair: int, a: str, b: str) -> tuple[str, str]:
    return (a, b) if pair % 2 == 0 else (b, a)


def problems(rec: dict) -> list[str]:
    """Why a live run is not a measurement (empty: it is one)."""
    if rec.get("error"):
        return [rec["error"]]
    out = []
    if rec.get("k6_job") != "succeeded":
        out.append(f"k6 job {rec.get('k6_job')}")
    if not rec.get("k6"):
        out.append(f"no k6 summary ({rec.get('k6_error', '')})")
    if rec.get("watch", {}).get("max_gap_s", 0.0) > MAX_POLL_GAP_S:
        out.append(f"pod polls {rec['watch']['max_gap_s']:.0f} s apart: the host stalled")
    if False in (rec.get("power", {}).get("start"), rec.get("power", {}).get("end")):
        out.append("on battery")
    return out


def attempt(arm: str, target: float, regime: dict, calibration: Path, path: Path) -> dict | None:
    """One arm's run of a pair, repeated while it is not a measurement; every
    invalid attempt is kept beside it. None when all attempts failed."""
    import live

    for i in range(1, ATTEMPTS + 1):
        try:
            rec = live.run(arm, target, regime, calibration, path.parent)
        except Exception as err:  # one run's trouble (kubectl, a timeout) must not end a 72-hour pilot
            rec = {"arm": arm, "error": f"{type(err).__name__}: {err}"[:400]}
        rec["attempt"], rec["problems"] = i, problems(rec)
        if not rec["problems"]:
            path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            return rec
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / f"{arm}.attempt{i}.invalid.json").write_text(json.dumps(rec, indent=1, default=str),
                                                                    encoding="utf-8")
    return None


def j_live(rec: dict, kappa: float = KAPPA) -> float:
    k = rec["k6"]
    return rec["watch"]["cost"] + kappa * k["requests"] * (1.0 - k["attainment"])


def sample_sizes(diffs: list[float], comparator_mean_j: float, predicted_gain: float) -> dict:
    sd = statistics.stdev(diffs) if len(diffs) > 1 else math.nan
    delta = abs(predicted_gain) * comparator_mean_j
    eps = EQUIVALENCE * comparator_mean_j
    n_sup = ((Z_ALPHA + Z_POWER) * sd / delta) ** 2 if delta > 0 else math.inf
    n_eq = ((Z_ALPHA + Z_ALPHA) * sd / eps) ** 2 if eps > 0 else math.inf
    return {"pairs": len(diffs), "mean_diff": statistics.fmean(diffs) if diffs else math.nan, "sigma_d": sd,
            "delta": delta, "n_superiority": math.ceil(n_sup) if math.isfinite(n_sup) else None,
            "n_equivalence": math.ceil(n_eq) if math.isfinite(n_eq) else None}


def contrasts(predictions: dict) -> list[dict]:
    """(regime, comparator arm, its target, the twin's predicted gain) of every contrast."""
    by = {(r["width"], r["sigma"]): r for r in predictions["regimes"]}
    out = []
    for lr in predictions["live_regimes"]:
        w, s = lr["width"], lr["sigma"]
        r = by[(w, s)]
        a1 = predictions["tuned_targets"][str(s)]["cpu_default"]
        out.append({"width": w, "sigma": s, "arm": "A3", "target": w, "gain": r["gain_vs_A3"]})
        out.append({"width": w, "sigma": s, "arm": "A1", "target": a1, "gain": r["gain_vs_A1"]})
    return out


def record_path(out: Path, c: dict, pair: int, arm: str) -> Path:
    return out / f"w{c['width']}_s{c['sigma']}" / f"A4_vs_{c['arm']}" / f"pair{pair}" / f"{arm}.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pairs", type=int, default=PAIRS)
    args = ap.parse_args(argv)
    import live
    import service_plant
    import workloads

    preds = json.loads(args.predictions.read_text(encoding="utf-8"))
    plant = service_plant.from_calibration(args.calibration)
    shapes = workloads.all_shapes()
    report = ["# WP17 live pilot", "", f"Generated by `testbed/hvpa/pilot.py`: {args.pairs} ABBA pairs per contrast; "
              f"J = cost + {KAPPA:g} x late requests; differences are comparator minus joint.", "",
              "| regime | contrast | pairs | mean diff | sigma_d | predicted gain | n superiority | n equivalence (+-2 %) |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    left_out = []
    for c in contrasts(preds):
        diffs, comp_j = [], []
        for k in range(args.pairs):
            reg = live.schedule(shapes[k % len(shapes)], c["sigma"], k, plant.slo_s, plant)
            js = {}
            for arm in order(k, "A4", c["arm"]):
                path = record_path(args.out, c, k, arm)
                if path.exists():
                    rec = json.loads(path.read_text(encoding="utf-8"))
                else:
                    target = c["width"] if arm == "A4" else c["target"]
                    rec = attempt(arm, float(target), reg, args.calibration, path)
                if rec is None:
                    break
                js[arm] = j_live(rec)
            if len(js) < 2:
                left_out.append(f"w{c['width']} s{c['sigma']} A4 vs {c['arm']} pair {k}")
                continue
            diffs.append(js[c["arm"]] - js["A4"])
            comp_j.append(js[c["arm"]])
        s = sample_sizes(diffs, statistics.fmean(comp_j) if comp_j else math.nan, c["gain"])
        report.append(f"| w{c['width']} s{c['sigma']} | A4 vs {c['arm']} | {s['pairs']} | {s['mean_diff']:+.5f} | "
                      f"{s['sigma_d']:.5f} | {c['gain']:+.3f} | {s['n_superiority']} | {s['n_equivalence']} |")
    report += ["", f"Pairs left out after {ATTEMPTS} invalid attempts of an arm: "
               + (", ".join(left_out) if left_out else "none") + "."]
    (args.out / "PILOT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
