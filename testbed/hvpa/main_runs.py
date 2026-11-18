"""WP17 main live runs (programme 2): the pilot's design at the size it asks for, the demand drawn.

The plan (WP17 step 5): per live regime, pairs in balanced order, as many as
the pilot's numbers ask by WP12's formulas. This is pilot.py's design run at
that size: a schedule is run once by each of A4, A3 and A1, back to back, in
pilot.order(k), and the joint arm's run serves both contrasts. Two things
differ from the pilot:

  size   n schedules per regime: the largest pair count the regime's
         contrasts need on the pilot's records (pilot.sample_sizes:
         superiority at the twin's predicted gain, or equivalence within
         +-2 %, per comparator as --test names; superiority by default),
         rounded up to a multiple of six so that every arm takes every place
         equally often;
  seeds  schedule k of a regime draws its demand with the seed
         seeds.run_seed(master, "wp17|w<width>|s<sigma>|k<k>"), the master seed
         of the registered draw (WP13), not k: nobody chooses the demand, and
         the pilot's is not reused.

    python main_runs.py plan --pilot ../out/hvpa/pilot --draw <draw.json> --out main_plan.json
    python main_runs.py run --plan main_plan.json --out ../out/hvpa/main

`plan` writes the plan, to be committed with the draw before any main run;
`run` refuses a plan that is not committed as it is on disk, and resumes from
its records (a run whose record exists is not repeated). Nothing here scores
the runs: the registered scorer reads the records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "research" / "hvpa"))
sys.path.insert(0, str(REPO / "research" / "registration"))

import pilot  # noqa: E402
import seeds  # noqa: E402

PREDICTIONS = REPO / "research" / "hvpa" / "predictions.json"
CALIBRATION = REPO / "research" / "hvpa" / "calibration.json"
TESTS = ("superiority", "equivalence")
BALANCE = len(pilot.ORDERS)   # six orders of the three arms
RUN_HOURS = 60.2 / 60         # one live run with its reset, as the pilot's runs took
Z_POWER_80 = 0.8416           # for the 80 %-power column of `sizes` (the plan's is 90 %)


def identity(width: int, sigma: float, k: int) -> str:
    return f"wp17|w{width}|s{sigma}|k{k}"


def pilot_differences(pilot_out: Path, predictions: dict) -> dict[tuple, dict]:
    """Per contrast, the pilot's paired differences J_B - J_A4 and the comparator's J."""
    out = {}
    for w, s, _targets, group in pilot.regimes(pilot.contrasts(predictions)):
        js = []
        for k in range(pilot.PAIRS):
            got = {}
            for arm in pilot.ARMS:
                path = pilot.record_path(pilot_out, w, s, k, arm)
                if path.exists():
                    got[arm] = pilot.j_live(json.loads(path.read_text(encoding="utf-8")))
            js.append(got)
        for c in group:
            pairs = [(j[c["arm"]], j["A4"]) for j in js if "A4" in j and c["arm"] in j]
            out[(w, s, c["arm"])] = {"diffs": [b - a for b, a in pairs], "comparator_j": [b for b, _ in pairs],
                                     "gain": c["gain"]}
    return out


def regime_size(contrast_sizes: dict[str, int | None]) -> int:
    """Schedules for a regime: the largest contrast's pairs, up to a multiple of six."""
    missing = [arm for arm, n in contrast_sizes.items() if n is None]
    if missing:
        raise SystemExit(f"the pilot cannot size the contrast(s) against {', '.join(missing)} "
                         "(no spread or no difference to detect): decide the size in the protocol")
    n = max(contrast_sizes.values())
    return max(BALANCE, BALANCE * math.ceil(n / BALANCE))


def sizes(pilot_out: Path, predictions: dict) -> list[dict]:
    """What the pilot's records so far ask for, per regime and contrast (exploratory; no draw needed):
    the pairs needed at the plan's 90 % power and at 80 %."""
    out = []
    for (w, s, arm), d in pilot_differences(pilot_out, predictions).items():
        if len(d["diffs"]) < 2:
            continue
        mean_j = statistics.fmean(d["comparator_j"])
        st = pilot.sample_sizes(d["diffs"], mean_j, d["gain"])
        n80 = ((pilot.Z_ALPHA + Z_POWER_80) * st["sigma_d"] / st["delta"]) ** 2 if st["delta"] > 0 else math.inf
        out.append({"width": w, "sigma": s, "arm": arm, **st, "comparator_j": mean_j,
                    "gain_observed": st["mean_diff"] / mean_j, "gain_predicted": d["gain"],
                    "n_superiority_80": math.ceil(n80) if math.isfinite(n80) else None})
    return out


def hours(schedules: int) -> float:
    """Testbed hours for `schedules` schedules, each run once by every arm."""
    return len(pilot.ARMS) * schedules * RUN_HOURS


def print_sizes(rows: list[dict]) -> None:
    for r in rows:
        print(f"w{r['width']} s{r['sigma']} A4 vs {r['arm']}: {r['pairs']} pairs; gain {r['gain_observed']:+.1%} "
              f"of {r['arm']}'s J (twin predicted {r['gain_predicted']:+.1%}); sigma_d {r['sigma_d']:.4f}; pairs "
              f"needed: superiority {r['n_superiority']} (at 80 % power {r['n_superiority_80']}), "
              f"equivalence {r['n_equivalence']}")
    for (w, s) in dict.fromkeys((r["width"], r["sigma"]) for r in rows):
        group = [r for r in rows if (r["width"], r["sigma"]) == (w, s)]
        for key, power in (("n_superiority", "90 %"), ("n_superiority_80", "80 %")):
            cells = []
            for name, picked in [("every contrast", group)] + [(f"A4 vs {r['arm']} only", [r]) for r in group]:
                ns = {r["arm"]: r[key] for r in picked}
                if None in ns.values():
                    cells.append(f"{name}: n/a")
                    continue
                n = regime_size(ns)
                cells.append(f"{name}: {n} schedules, {hours(n):.0f} h")
            print(f"w{w} s{s} main runs, superiority at {power} power, all arms on every schedule: " + "; ".join(cells))


def checked_master(draw: dict) -> str:
    """The draw's master seed, recomputed from its protocol digest and randomness,
    the randomness checked against the round's signature (drand's rule)."""
    try:
        seeds.checked({k: draw[k] for k in ("round", "randomness", "signature")}, draw["round"], "the draw")
    except (seeds.DrawError, KeyError) as err:
        raise SystemExit(f"not a valid draw: {err}") from err
    master = seeds.master_seed(draw["protocol_sha256"], draw["randomness"])
    if master != draw.get("master_seed"):
        raise SystemExit("the draw's master seed is not SHA-256(protocol digest || randomness)")
    return master


def make_plan(pilot_out: Path, predictions: dict, draw: dict, tests: dict[str, str]) -> dict:
    import workloads

    master = checked_master(draw)
    shapes = workloads.all_shapes()
    diffs = pilot_differences(pilot_out, predictions)
    plan = {"design": "WP17 main live runs: testbed/hvpa/main_runs.py",
            "predictions_sha256": hashlib.sha256(json.dumps(predictions, sort_keys=True).encode()).hexdigest(),
            "draw": {"round": draw["round"], "master_seed": master, "protocol_sha256": draw["protocol_sha256"]},
            "tests": tests, "regimes": []}
    for w, s, targets, group in pilot.regimes(pilot.contrasts(predictions)):
        sizes, per = {}, {}
        for c in group:
            d = diffs[(w, s, c["arm"])]
            if len(d["diffs"]) < 2:
                raise SystemExit(f"w{w} s{s} A4 vs {c['arm']}: {len(d['diffs'])} pilot pair(s); the pilot is not done")
            st = pilot.sample_sizes(d["diffs"], statistics.fmean(d["comparator_j"]), d["gain"])
            test = tests.get(c["arm"], "superiority")
            sizes[c["arm"]] = st["n_superiority"] if test == "superiority" else st["n_equivalence"]
            per[c["arm"]] = {**st, "test": test}
        n = regime_size(sizes)
        plan["regimes"].append({
            "width": w, "sigma": s, "targets": targets, "contrasts": per, "schedules_n": n,
            "schedules": [{"k": k, "shape": shapes[k % len(shapes)], "order": list(pilot.order(k)),
                           "seed": seeds.run_seed(master, identity(w, s, k))} for k in range(n)]})
    return plan


def run_plan(plan_path: Path, out: Path, calibration: Path = CALIBRATION) -> int:
    import live
    import run_pilot
    import service_plant

    for path in (plan_path, calibration, PREDICTIONS):
        if not run_pilot.committed_unchanged(path):
            raise SystemExit(f"{path.name} is not committed as it is on disk: no main run")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plant = service_plant.from_calibration(calibration)
    lost = 0
    for r in plan["regimes"]:
        for sc in r["schedules"]:
            reg = live.schedule(sc["shape"], r["sigma"], sc["seed"], plant.slo_s, plant)
            js = pilot.run_schedule(out, r["width"], r["sigma"], sc["k"], r["targets"], reg, calibration)
            lost += len(pilot.ARMS) - len(js)
            print(f"w{r['width']} s{r['sigma']} schedule {sc['k']}: {len(js)} of {len(pilot.ARMS)} arms measured",
                  flush=True)
    print(f"main runs done; {lost} run(s) left out after {pilot.ATTEMPTS} invalid attempts")
    return 0


def parse_tests(items: list[str]) -> dict[str, str]:
    tests = {}
    for item in items:
        arm, _, test = item.partition("=")
        if arm not in ("A3", "A1") or test not in TESTS:
            raise SystemExit(f"--test takes A3=<test> or A1=<test>, test one of {TESTS}; not {item!r}")
        tests[arm] = test
    return tests


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--pilot", type=Path, required=True, help="the pilot's records (testbed/out/hvpa/pilot)")
    p.add_argument("--draw", type=Path, required=True, help="the registered draw (research/registration)")
    p.add_argument("--predictions", type=Path, default=PREDICTIONS)
    p.add_argument("--test", nargs="*", default=[], help="A3=superiority|equivalence, A1=... (default superiority)")
    p.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", type=Path, required=True)
    r.add_argument("--calibration", type=Path, default=CALIBRATION)
    r.add_argument("--out", type=Path, required=True)
    z = sub.add_parser("sizes", help="what the pilot's records so far ask for (exploratory, no draw)")
    z.add_argument("--pilot", type=Path, required=True)
    z.add_argument("--predictions", type=Path, default=PREDICTIONS)
    args = ap.parse_args(argv)
    if args.cmd == "sizes":
        print_sizes(sizes(args.pilot, json.loads(args.predictions.read_text(encoding="utf-8"))))
        return 0
    if args.cmd == "plan":
        if args.out.exists():
            raise SystemExit(f"{args.out} exists: a plan is written once")
        plan = make_plan(args.pilot, json.loads(args.predictions.read_text(encoding="utf-8")),
                         json.loads(args.draw.read_text(encoding="utf-8")), parse_tests(args.test))
        args.out.write_text(json.dumps(plan, indent=1), encoding="utf-8")
        for reg in plan["regimes"]:
            print(f"w{reg['width']} s{reg['sigma']}: {reg['schedules_n']} schedules "
                  f"({len(pilot.ARMS) * reg['schedules_n']} one-hour runs)")
        return 0
    return run_plan(args.plan, args.out, args.calibration)


if __name__ == "__main__":
    sys.exit(main())
