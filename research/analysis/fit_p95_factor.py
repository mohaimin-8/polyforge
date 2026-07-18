"""Fit the measured p95/mean tail factor for the Wave 5 latency-model
adoption (PREREG_LM_ADOPTION.md).

Input is the *published* Phase 6 calibration table
(`research/calibration/congestion_runs.csv`, committed with
CALIBRATION.md) — no campaign outcome touches this fit, so freezing its
output in the prereg is contamination-free.

The sim asserts p95 = mean * 1.4 with the factor flat in utilization; the
calibration measured p95/mean rising from 1.59 at rho 0.20 to 2.41 at rho
0.91. The adopted form mirrors the congestion family so one shape carries
both:

    F(rho) = f0 * (1 - min(rho, SATURATION_RHO)) ** (-b)

fit by least squares in log space: ln F = ln f0 - b * ln(1 - rho).
The clamp at SATURATION_RHO (0.95) keeps the factor finite in the
overload branch, exactly as the congestion curve's quadratic device does.

Output: `p95_tail_fit.json` next to this script + a console table. The
fitted (f0, b) are frozen into PREREG_LM_ADOPTION.md before the matrix
runs; model.set_model_form(p95_tail=(f0, b)) applies them.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV = HERE.parent / "calibration" / "congestion_runs.csv"
OUT = HERE / "p95_tail_fit.json"

FLAT_FACTOR = 1.4  # the published model constant (model.P95_FACTOR)


def main() -> None:
    rows = list(csv.DictReader(CSV.open(newline="")))
    pts = [(float(r["rho_offered"]), float(r["p95_over_mean"])) for r in rows]

    # Least squares on ln F = ln f0 - b * ln(1 - rho).
    xs = [-math.log(1.0 - rho) for rho, _ in pts]
    ys = [math.log(f) for _, f in pts]
    n = len(pts)
    xbar, ybar = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    b = sxy / sxx
    ln_f0 = ybar - b * xbar
    f0 = math.exp(ln_f0)

    ss_res = sum((y - (ln_f0 + b * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot else float("nan")
    # The flat published factor, scored on the same log-space points.
    ss_flat = sum((y - math.log(FLAT_FACTOR)) ** 2 for y in ys)
    r2_flat = 1.0 - ss_flat / ss_tot if ss_tot else float("nan")

    print(f"fitted F(rho) = {f0:.4f} * (1 - rho)^(-{b:.4f})   [log-space R^2 {r2:.3f}]")
    print(f"flat 1.4 on the same points: log-space R^2 {r2_flat:.3f}")
    print(f"{'rho':>6} {'measured':>9} {'fitted':>7} {'flat':>5}")
    fitted_pts = []
    for rho, f in pts:
        fit = f0 * (1.0 - rho) ** (-b)
        fitted_pts.append({"rho": rho, "measured": f, "fitted": round(fit, 4)})
        print(f"{rho:6.2f} {f:9.3f} {fit:7.3f} {FLAT_FACTOR:5.2f}")

    OUT.write_text(json.dumps({
        "form": "F(rho) = f0 * (1 - min(rho, 0.95)) ** (-b)",
        "f0": round(f0, 4),
        "b": round(b, 4),
        "r2_log_space": round(r2, 4),
        "r2_flat_1_4": round(r2_flat, 4),
        "source": str(CSV.relative_to(HERE.parent.parent)),
        "points": fitted_pts,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
