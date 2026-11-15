"""WP17 live pilot: paired runs per regime and contrast, then the pairs needed (programme 2).

From research/hvpa/predictions.json (committed before any live run): the live
regimes (width, volatility) and the tuned HPA targets. Per regime, two
contrasts: joint (A4) against per-knob (A3) at the regime's width, and joint
against the HPA-on-CPU + VPA pair (A1, default VPA flags, the target tuned for
the regime's volatility). Per regime, PAIRS schedules (schedule k: shape k
mod the shapes, seed k), each run once by each of the three arms, back to
back, in the six orders of the three in turn: over six schedules every arm
takes every place twice and, within each contrast, the comparator runs first
on half of them, the balance ABBA pairs give (plan WP12). The joint arm's run
on a schedule serves both contrasts: before 2026-11-15 it was repeated on the
same inputs, once per contrast (24 runs per regime; now 18). Each run is
live.run; its J is cost + KAPPA x late requests (late = requests x (1 -
attainment)), the twin's objective. Then, per contrast (plan WP12):
  sigma_d   the standard deviation of the paired differences J_B - J_A4 over
            the schedules;
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
import itertools
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


ARMS = ("A4", "A3", "A1")
ORDERS = tuple(itertools.permutations(ARMS))


def order(k: int) -> tuple[str, ...]:
    """Schedule k's run order: the six orders of the three arms in turn."""
    return ORDERS[k % len(ORDERS)]


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
        except Exception as err:  # one run's trouble (kubectl, a timeout) must not end a days-long pilot
            rec = {"arm": arm, "error": f"{type(err).__name__}: {err}"[:400]}
        rec["attempt"], rec["problems"] = i, problems(rec)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rec["problems"]:
            path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            return rec
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


def regimes(cs: list[dict]) -> list[tuple[int, float, dict, list[dict]]]:
    """(width, sigma, each arm's target, the regime's contrasts) per live regime, in order."""
    by: dict[tuple, list[dict]] = {}
    for c in cs:
        by.setdefault((c["width"], c["sigma"]), []).append(c)
    return [(w, s, {"A4": w, **{c["arm"]: c["target"] for c in group}}, group) for (w, s), group in by.items()]


def record_path(out: Path, width: int, sigma: float, k: int, arm: str) -> Path:
    return out / f"w{width}_s{sigma}" / f"schedule{k}" / f"{arm}.json"


def run_schedule(out: Path, width: int, sigma: float, k: int, targets: dict, reg: dict,
                 calibration: Path) -> dict[str, float]:
    """J of each arm's valid run on schedule k, the arms in order(k). A lost
    joint run ends the schedule: neither contrast could use the others."""
    js = {}
    for arm in order(k):
        path = record_path(out, width, sigma, k, arm)
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
        else:
            rec = attempt(arm, float(targets[arm]), reg, calibration, path)
        if rec is None:
            if arm == "A4":
                break
            continue
        js[arm] = j_live(rec)
    return js


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
    report = ["# WP17 live pilot", "",
              f"Generated by `testbed/hvpa/pilot.py`: {args.pairs} schedules per regime, each run once by A4, A3 "
              "and A1 back to back in the six orders of the three in turn, so each contrast has a paired "
              f"difference per schedule; J = cost + {KAPPA:g} x late requests; differences are comparator minus "
              "joint.", "",
              "| regime | contrast | pairs | mean diff | sigma_d | predicted gain | n superiority | n equivalence (+-2 %) |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    left_out = []
    for w, sigma, targets, group in regimes(contrasts(preds)):
        per_schedule = [run_schedule(args.out, w, sigma, k, targets,
                                     live.schedule(shapes[k % len(shapes)], sigma, k, plant.slo_s, plant),
                                     args.calibration) for k in range(args.pairs)]
        for c in group:
            diffs, comp_j = [], []
            for k, js in enumerate(per_schedule):
                if "A4" not in js or c["arm"] not in js:
                    left_out.append(f"w{w} s{sigma} A4 vs {c['arm']} schedule {k}")
                    continue
                diffs.append(js[c["arm"]] - js["A4"])
                comp_j.append(js[c["arm"]])
            st = sample_sizes(diffs, statistics.fmean(comp_j) if comp_j else math.nan, c["gain"])
            report.append(f"| w{w} s{sigma} | A4 vs {c['arm']} | {st['pairs']} | {st['mean_diff']:+.5f} | "
                          f"{st['sigma_d']:.5f} | {c['gain']:+.3f} | {st['n_superiority']} | {st['n_equivalence']} |")
    report += ["", f"Pairs left out after {ATTEMPTS} invalid attempts of an arm: "
               + (", ".join(left_out) if left_out else "none") + "."]
    (args.out / "PILOT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
