"""WP17's live machinery checked end to end before the pilot (programme 2).

Not a measurement: no number here enters any analysis. One short run per
pilot arm (A4, A3, A1) at the pilot's targets for its first live regime, on a
7-minute schedule built for the check: 2 minutes at the twin's base rate,
3 at three times it (1.8 x what the initial 2 pods x 0.5 core serve), 2 at
the base again. It runs only when the predictions and the calibration are
committed as they are on disk, so no live WP17 run comes before the
predictions. Each run must pass pilot.problems and:
  load     k6 sent requests;
  grew     the service grew under the high phase (a pod added or resized);
  planner  (A3, A4) at least MIN_DECISIONS decisions, every act answered
           2xx, a request rate near the load read from Prometheus;
  hpa/vpa  (A1) the HPA read a CPU utilisation and the VPA had a
           recommendation by the run's end.
Writes SMOKE.md and one record per arm into smoke_runs/ (the queue commits
them), then resets the cluster so no arm runs on while the CPU campaigns do.
Exit 0 when every check passed, 1 when one failed (SMOKE.md says which), 3
when the predictions or the calibration are not committed.

    python smoke_live.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "research" / "hvpa"))

import run_pilot  # noqa: E402

OUT = HERE / "smoke_runs"
SCRATCH = run_pilot.OUT.parent / "smoke"
PHASES = ((12, 1.0), (18, 3.0), (12, 1.0))   # (10 s steps, multiple of the twin's base rate)
MIN_DECISIONS = 6                             # one per lattice.PLAN_S = 30 s gives about 14
OK_STATUS = (200, 201)
NEAR_LOAD = 0.5                               # a rate read below half the base rate is no reading


def smoke_regime(plant) -> dict:
    """The check's schedule in live.schedule's form."""
    import live
    import workloads

    base = workloads.base_rps(plant)
    return {"step_s": live.STEP_S, "rates": [round(base * m, 4) for n, m in PHASES for _ in range(n)],
            "slo_s": plant.slo_s, "shape": "smoke", "sigma": 0.0, "seed": 0}


def pilot_targets(pred: dict) -> dict[str, float]:
    """Each arm's target in the pilot's first live regime."""
    import pilot

    return pilot.regimes(pilot.contrasts(pred))[0][2]


def planner_checks(decisions: list[dict], base_rps: float) -> list[str]:
    out = []
    if len(decisions) < MIN_DECISIONS:
        out.append(f"{len(decisions)} planner decisions, fewer than {MIN_DECISIONS}")
    bad = [act for d in decisions for act in d.get("acts", []) if act[-1] not in OK_STATUS]
    if bad:
        out.append(f"{len(bad)} planner acts not answered 2xx, the first {bad[0]}")
    top = max((d.get("rate_rps") or 0.0 for d in decisions), default=0.0)
    if top < NEAR_LOAD * base_rps:
        out.append(f"the planner read no rate near the load from Prometheus (at most {top:.2f}/s, "
                   f"base {base_rps:.2f}/s)")
    return out


def a1_objects() -> dict:
    """The HPA's and the VPA's status at the run's end (before the next reset)."""
    import live

    got = {}
    for key, kind in (("hpa", "hpa"), ("vpa", "verticalpodautoscaler.autoscaling.k8s.io")):
        items = json.loads(live.kubectl("-n", live.NAMESPACE, "get", kind, "-o", "json")).get("items", [])
        got[key] = [item.get("status", {}) for item in items]
    return got


def a1_checks(objects: dict) -> list[str]:
    if objects.get("error"):
        return [f"the HPA's and the VPA's status could not be read ({objects['error']})"]
    out = []
    used = [m.get("resource", {}).get("current", {}).get("averageUtilization")
            for status in objects.get("hpa", []) for m in status.get("currentMetrics") or []]
    if not any(isinstance(u, (int, float)) for u in used):
        out.append("the HPA read no CPU utilisation (metrics-server)")
    recs = [c for status in objects.get("vpa", [])
            for c in (status.get("recommendation") or {}).get("containerRecommendations") or []]
    if not recs:
        out.append("the VPA had no recommendation by the run's end")
    return out


def checks(rec: dict, base_rps: float) -> list[str]:
    """Why a smoke run shows the machinery broken (empty: it works)."""
    import live
    import pilot

    out = pilot.problems(rec)
    if rec.get("error"):
        return out
    if not (rec.get("k6") or {}).get("requests"):
        out.append("k6 sent no request")
    watch = rec.get("watch", {})
    if watch.get("max_replicas", 0) <= live.INITIAL[0] and not watch.get("resizes"):
        out.append("the service never grew: no pod added and none resized under three times the base rate")
    if rec.get("arm") in ("A3", "A4"):
        out += planner_checks(rec.get("decisions", []), base_rps)
    if rec.get("arm") == "A1":
        out += a1_checks(rec.get("objects", {}))
    return out


def run_arm(arm: str, target: float, regime: dict, base_rps: float) -> dict:
    import live

    try:
        rec = live.run(arm, target, regime, run_pilot.CALIBRATION, SCRATCH)
    except Exception as err:  # the check reports a run's trouble, it does not end on it
        rec = {"arm": arm, "error": f"{type(err).__name__}: {err}"[:400]}
    if arm == "A1" and not rec.get("error"):
        try:
            rec["objects"] = a1_objects()
        except Exception as err:
            rec["objects"] = {"error": f"{type(err).__name__}: {err}"[:300]}
    rec["target"], rec["problems"] = target, checks(rec, base_rps)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{arm}.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return rec


def row(rec: dict) -> str:
    k6, watch = rec.get("k6") or {}, rec.get("watch", {})
    att = k6.get("attainment")
    return (f"| {rec['arm']} | {rec['target']:g} | {rec.get('k6_job', '-')} | {k6.get('requests', '-')} | "
            f"{f'{att:.3f}' if isinstance(att, float) else '-'} | {watch.get('max_replicas', '-')} | "
            f"{watch.get('resizes', '-')} | {len(rec['decisions']) if 'decisions' in rec else '-'} | "
            f"{'; '.join(rec['problems']) or 'none'} |")


def report(recs: list[dict], base_rps: float, reset_error: str) -> str:
    failed = [r["arm"] for r in recs if r["problems"]]
    lines = ["# WP17 live smoke", "",
             "Generated by `testbed/hvpa/smoke_live.py` before the pilot. Not a measurement: no number here enters "
             "any analysis. One run per pilot arm at the pilot's targets for its first live regime, on a 7-minute "
             f"schedule: 2 min at the twin's base rate ({base_rps:.2f} requests/s), 3 min at three times it, 2 min "
             "at the base.", "",
             "| arm | target | k6 job | requests | attainment | max pods | resizes | planner decisions | problems |",
             "|---|---:|---|---:|---:|---:|---:|---:|---|", *map(row, recs), ""]
    lines.append(f"Failed: {', '.join(failed)}." if failed else "Every check passed.")
    if reset_error:
        lines.append(f"The reset after the runs failed ({reset_error}): an arm may still be running.")
    return "\n".join(lines) + "\n"


def main() -> int:
    for path in (run_pilot.PREDICTIONS, run_pilot.CALIBRATION):
        if not run_pilot.committed_unchanged(path):
            print(f"{path.name} is not committed as it is on disk: no live run", flush=True)
            return 3
    import live
    import pilot
    import service_plant
    import workloads

    targets = pilot_targets(json.loads(run_pilot.PREDICTIONS.read_text(encoding="utf-8")))
    plant = service_plant.from_calibration(run_pilot.CALIBRATION)
    regime, base = smoke_regime(plant), workloads.base_rps(plant)
    try:
        run_pilot.kubectl("-n", "pf-testbed", "scale", "deployment", "--all", "--replicas=0")
        run_pilot.kubectl("-n", "pf-hvpa", "wait", "--for=condition=available", "deployment", "--all",
                          "--timeout=900s")
    except subprocess.CalledProcessError as err:
        return write(f"# WP17 live smoke\n\nNot run: the cluster was not ready ({(err.stderr or '').strip()[:300]}).\n",
                     1)
    recs = [run_arm(arm, float(targets[arm]), regime, base) for arm in pilot.ARMS]
    reset_error = ""
    try:
        live.reset()
    except Exception as err:  # reported: the CPU campaigns would run beside a live arm
        reset_error = f"{type(err).__name__}: {err}"[:300]
    return write(report(recs, base, reset_error), 1 if reset_error or any(r["problems"] for r in recs) else 0)


def write(text: str, rc: int) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "SMOKE.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
