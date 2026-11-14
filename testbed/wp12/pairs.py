"""WP12 live pilot: the arms' committed plans replayed on the testbed in ABBA pairs (programme 2).

Refuses to start unless plans.json is committed and unchanged (the plans come
first). Per regime and pair k (plan WP12), the joint and the per-knob plan of
the pair's live schedule run back to back, joint first for even k and
per-knob first for odd k (ABBA); pairs alternate the regimes. One arm's run:
  1. posture  the plan's step 0: each tier's pods through the actuator, ready
              before the load; a fresh gateway (cold cache, knobs in memory);
              each tenant's tier pin and cache written and read back; the
              model servers quiet (run_cells.py's steps 1-3);
  2. load     k6 replays the schedule open loop on a clock fixed in advance
              (START_UNIX_MS) while the actuator replays the plan's later
              decisions on the same clock (actuate.replay), logging when each
              scaled tier is ready;
  3. measure  attainment over every scheduled request after the warm-up (a
              failure or a missing sample is late), each tenant-step's late
              share, cost = cores held x price over the scored window from the
              actuator's own log, and J = WP9's objective for the two tenants
              (tb_planner.j_of).
A run is not a measurement, and is repeated (at most ATTEMPTS times, then its
pair is left out), when: the posture did not read back; a plan decision was
refused, failed a knob write or the replay did not finish; k6's schedule lag
p99 exceeded 1 s; more than 1 % of the samples are missing; a model-server pod
served less than half its fair share of tokens over a window in which its
tier's pool did not change (check_run's test, judged only where the pool was
stable: a pod the plan adds mid-run cannot have a whole-run fair share); or the
host was off the mains. Resumable: a valid run's record is never repeated.

Per regime and metric (J, cost per 1k requests, attainment), the paired
differences d = per_knob - joint give sigma_d and the pairs needed at
alpha 0.05 and power 0.9 (plan WP12): superiority at the planners' own
predicted mean difference, and equivalence within +-2 % of the joint arm's
mean. Writes PILOT.md and pilot.json.

    python pairs.py --schedules ../out/wp12/schedules --tokens ../out/tokens.json \
        --admin-key-file ../out/admin-key.txt --out ../out/wp12/runs
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
for _p in (HERE, TESTBED / "validation", TESTBED / "actuator", TESTBED / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import tb_planner as tbp  # noqa: E402

import actuate  # noqa: E402
import cell_schedules as cs  # noqa: E402
import run_cells as rc  # noqa: E402
from check_run import (by_labels, distribution_findings, judged, live_pods, pod_shares,  # noqa: E402
                       prometheus_query, read_k6_requests)

PLANS = HERE / "plans.json"
ATTEMPTS = 3
START_LEAD_S = 30.0
REPLAY_JOIN_S = 420.0
MAX_MISSING_SHARE = 0.01
STABLE_MIN_S = 120.0
SETTLE_S = 15.0
EQUIVALENCE = 0.02
Z_ALPHA, Z_POWER = 1.645, 1.282
G3_MAX_PAIRS = 40
METRICS = ("J", "cost_per_1k", "attainment")
K6_TIMEOUT_S = cs.DURATION_S + 900 + 300


def order(k: int) -> tuple[str, str]:
    return ("joint", "per_knob") if k % 2 == 0 else ("per_knob", "joint")


def run_order(regimes: Sequence[str], pairs: int) -> list[tuple[str, int]]:
    out = []
    for k in range(pairs):
        out += [(r, k) for r in (regimes if k % 2 == 0 else tuple(reversed(regimes)))]
    return out


# -------------------------------------------------------------------- measuring

def cores_timeline(records: Sequence[dict], cores: dict[str, int]) -> list[tuple[float, int]]:
    """(time, cores held) at every replica decision the actuator applied, from
    its log: the first decision's full target, then each change merged in."""
    spec: dict[str, int] = {}
    out = []
    for r in records:
        if r.get("event") != "decision" or "applied" not in r:
            continue
        spec = {**r.get("previous", {}), **spec, **r["applied"]}
        out.append((float(r["applied_unix"]), sum(n * cores[t] for t, n in spec.items())))
    return out


def held_cost(timeline: Sequence[tuple[float, int]], t0: float, t1: float, price_core_hr: float) -> float:
    """Core-hours held over [t0, t1] x price (cores between two decisions are
    the earlier decision's)."""
    if not timeline or timeline[0][0] > t0:
        raise ValueError("the cores held at the window's start are not in the log")
    total = 0.0
    for i, (t, c) in enumerate(timeline):
        a = max(t, t0)
        b = min(timeline[i + 1][0] if i + 1 < len(timeline) else math.inf, t1)
        if b > a:
            total += c * (b - a)
    return total / 3600.0 * price_core_hr


def late_shares(table: Sequence[dict], samples: Sequence[dict], slo_ms: float,
                tenants: Sequence[str]) -> tuple[np.ndarray, np.ndarray, int]:
    """(requests, late) per scored step x tenant over every scheduled request,
    and how many had no sample. Late: no sample, not a 200, or over the SLO."""
    by_seq = {r["seq"]: r for r in samples}
    steps = cs.STEPS - tbp.WARMUP_STEPS
    n, late, missing = np.zeros((steps, len(tenants))), np.zeros((steps, len(tenants))), 0
    for row in table:
        if row["warmup"]:
            continue
        k, j = int(row["t"] // tbp.BIN_S) - tbp.WARMUP_STEPS, list(tenants).index(row["tenant"])
        n[k, j] += 1
        s = by_seq.get(row["seq"])
        missing += s is None
        if s is None or s["status"] != "200" or s["latency_ms"] > slo_ms:
            late[k, j] += 1
    return n, late, missing


def measure(table: Sequence[dict], samples: Sequence[dict], records: Sequence[dict], start: float,
            testbed: dict) -> dict:
    tenants = sorted(testbed["pins"])
    cores = {t: testbed["tiers"][t]["cores_per_pod"] for t in testbed["tiers"]}
    n, late, missing = late_shares(table, samples, testbed["slo_ms"], tenants)
    viol = np.divide(late, n, out=np.zeros_like(late), where=n > 0)
    t0, t1 = start + cs.WARMUP_S, start + cs.DURATION_S
    cost = held_cost(cores_timeline(records, cores), t0, t1, testbed["price_core_hr"])
    j = tbp.j_of(cost, n.shape[0], viol)
    total = float(n.sum())
    ok = [s["latency_ms"] for s in samples if not s["warmup"] and s["status"] == "200"]
    return {**j, "requests": total, "late": float(late.sum()), "missing_samples": missing,
            "attainment": 1.0 - float(late.sum()) / total if total else math.nan,
            "cost_per_1k": 1000.0 * cost / total if total else math.nan,
            "mean_latency_s": float(np.mean(ok)) / 1000 if ok else math.nan,
            "hit_rate": (sum(1 for s in samples if not s["warmup"] and s["status"] == "200" and s["cache"] == "hit")
                         / len(ok)) if ok else math.nan}


def stable_windows(records: Sequence[dict], tier: str, t0: float, t1: float,
                   min_s: float = STABLE_MIN_S) -> list[tuple[float, float, int]]:
    """(from, to, pods) of the stretches inside [t0, t1] in which the tier's
    pool did not change and every pod was ready (SETTLE_S after the ready
    event); a change never reported ready opens no window."""
    marks = []  # (time the pool became stable, pods), then the next change ends it
    changes = [r for r in records if r.get("event") == "decision" and tier in r.get("applied", {})]
    for i, r in enumerate(changes):
        n, applied = r["applied"][tier], float(r["applied_unix"])
        prev = changes[i - 1]["applied"][tier] if i else None
        if prev is not None and prev == n:
            continue
        ready = applied if i == 0 else next(
            (float(e["ready_unix"]) for e in records if e.get("event") == "ready" and e.get("tier") == tier
             and float(e["ready_unix"]) >= applied and e.get("replicas") == n), math.inf)
        marks.append((applied, ready + SETTLE_S, n))
    out = []
    for i, (_applied, stable_from, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else t1
        a, b = max(stable_from, t0), min(end, t1)
        if n >= 2 and b - a >= min_s:
            out.append((a, b, n))
    return out


def spread_findings(query: Callable, records: Sequence[dict], tiers: Sequence[str], t0: float, t1: float) -> dict:
    """check_run's load-spread test over each tier's stable windows."""
    findings, judged_windows = [], []
    for tier in tiers:
        for a, b, n in stable_windows(records, tier, t0, t1):
            w = f"{max(1, int(b - a))}s"
            tokens = {k: v for k, v in by_labels(query(
                f"sum by (tier, pod) (increase(llamacpp:tokens_predicted_total[{w}]))", b), "tier", "pod").items()
                if k[0] == tier}
            kept, skipped = judged(tokens, live_pods(query, b))
            judged_windows.append({"tier": tier, "from": a, "to": b, "pods": n, "skipped": skipped.get(tier)})
            findings += [f"[{a:.0f}, {b:.0f}] {f}" for f in distribution_findings(pod_shares(kept))]
    return {"windows": judged_windows, "findings": findings}


# -------------------------------------------------------------------- one run

def problems_of(rec: dict) -> list[str]:
    out = list(rec.get("posture", {}).get("errors", []))
    if rec.get("error"):
        out.append(rec["error"])
    if rec.get("replay_failed"):
        out.append(f"{rec['replay_failed']} plan decisions refused or failed")
    if rec.get("replay_alive") or rec.get("replay_error"):
        out.append(f"the plan replay did not finish cleanly ({rec.get('replay_error', 'still running')})")
    if rec.get("k6_exit") not in (0, 99):  # 99: a threshold failed, which the lag rule below judges
        out.append(f"k6 exit {rec.get('k6_exit')}")
    if rec.get("lag_p99_ms", math.inf) > rc.MAX_P99_LAG_MS:
        out.append(f"schedule lag p99 {rec.get('lag_p99_ms')} ms")
    m = rec.get("measured") or {}
    if not m:
        out.append("not measured")
    elif m["missing_samples"] > MAX_MISSING_SHARE * max(1.0, m["requests"]):
        out.append(f"{m['missing_samples']} of {m['requests']:.0f} scored requests have no sample")
    if rec.get("spread", {}).get("findings"):
        out.append("load spread inside a stable pool: see run.json")
    if False in (rec.get("ac_power"), rec.get("ac_power_end")):
        out.append("on battery")
    return out


def set_posture(actuator: actuate.Actuator, tenants: dict) -> dict:
    knobs = {t: actuate.Knobs(model_tier=k["model_tier"], cache_size_mb=int(k["cache_size_mb"]))
             for t, k in tenants.items()}
    for attempt in range(1, rc.POSTURE_ATTEMPTS + 1):
        record = actuator.apply(actuate.Decision(step=None, t_s=0.0, tenants=knobs))
        if not record["errors"]:
            break
        time.sleep(2.0 * attempt)
    return {"knobs": record["knobs"], "errors": record["errors"], "attempts": attempt}


class RunActuator(actuate.Actuator):
    """The actuator of one run: once the run is over (`close`), any decision
    its replay still holds is refused, so a late replay can never act in the
    next run."""

    closed = False

    def close(self) -> None:
        self.closed = True

    def apply(self, decision, due_unix=None):
        if self.closed:
            raise actuate.DecisionError("the run is over: decision not applied")
        return super().apply(decision, due_unix)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def run_arm(plan: dict, testbed: dict, schedule_dir: Path, table: list[dict], tokens: Path, admin_key: str,
            out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    bounds = actuate.Bounds.load(actuate.DEFAULT_BOUNDS)
    log_path = out / "actuator.jsonl"
    if log_path.exists():
        log_path.unlink()
    actuator = RunActuator(bounds=bounds, kube=actuate.KubectlKube(bounds.namespace, context=rc.CONTEXT),
                           knobs=actuate.GatewayKnobs(rc.GATEWAY, admin_key), log=actuate.jsonl_logger(log_path))
    d0 = plan["decisions"][0]
    rec: dict = {"schedule": plan["schedule"], "regime": plan["regime"], "k": plan["k"], "arm": plan["arm"],
                 "ac_power": rc.on_ac_power(), "complete": False}
    rec["initial"] = actuator.apply(actuate.Decision(step=0, t_s=0.0, replicas=d0["replicas"])).get("applied")
    rec["ready_wait_s"] = rc.wait_ready(actuator)
    rec["gateway_restart_s"] = rc.fresh_gateway()
    rec["posture"] = set_posture(actuator, d0["tenants"])
    rec["quiet_wait_s"] = rc.wait_quiet(prometheus_query(rc.PROMETHEUS))
    later = [actuate.parse_decision(d, bounds) for d in plan["decisions"][1:]]
    start = time.time() + START_LEAD_S
    outcome: dict = {}

    def replay() -> None:
        try:
            outcome["failed"] = actuate.replay(actuator, later, start)
        except Exception as err:  # noqa: BLE001 (recorded; the run is then not a measurement)
            outcome["error"] = f"{type(err).__name__}: {err}"[:300]

    env = {**os.environ, **rc.K6_ENV, "SCHEDULE": str((schedule_dir / "schedule.json").resolve()),
           "TOKENS": str(tokens.resolve()), "GATEWAY_URL": rc.GATEWAY, "START_UNIX_MS": str(int(start * 1000))}
    thread = threading.Thread(target=replay, daemon=True)
    with (out / "k6.log").open("w", encoding="utf-8") as k6_log:
        proc = subprocess.Popen(["k6", "run", "--no-usage-report", "--summary-export", str(out / "k6-summary.json"),
                                 "--out", f"csv={out / 'k6-samples.csv.gz'}", str(TESTBED / "load" / "replay.js")],
                                env=env, stdout=k6_log, stderr=subprocess.STDOUT)
        thread.start()
        try:
            rec["k6_exit"] = proc.wait(timeout=K6_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            rec["k6_exit"] = "timeout"
    thread.join(timeout=REPLAY_JOIN_S)
    actuator.close()
    rec.update(start_unix=start, end_unix=time.time(), ac_power_end=rc.on_ac_power(),
               lag_p99_ms=rc.k6_lag_p99(out / "k6-summary.json"), replay_failed=outcome.get("failed"),
               replay_alive=thread.is_alive(), replay_error=outcome.get("error"))
    records = read_jsonl(log_path)
    samples = read_k6_requests(out / "k6-samples.csv.gz") if (out / "k6-samples.csv.gz").exists() else []
    rec["measured"] = measure(table, samples, records, start, testbed) if samples else {}
    rec["spread"] = spread_findings(prometheus_query(rc.PROMETHEUS), records, sorted(testbed["tiers"]),
                                    start + cs.WARMUP_S, start + cs.DURATION_S)
    rec["ready_after_s"] = [e["after_s"] for e in records if e.get("event") == "ready" and e.get("direction") == "up"]
    rec["problems"] = problems_of(rec)
    rec.update(valid=not rec["problems"], complete=True)
    (out / "run.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    return rec


def attempt_dir(out: Path, regime: str, k: int, arm: str, i: int) -> Path:
    return out / regime / f"pair{k}" / arm / f"attempt{i}"


def valid_run(out: Path, regime: str, k: int, arm: str) -> dict | None:
    for i in range(1, ATTEMPTS + 1):
        p = attempt_dir(out, regime, k, arm, i) / "run.json"
        if p.exists():
            rec = json.loads(p.read_text(encoding="utf-8"))
            if rec.get("valid"):
                return rec
    return None


def run_pair_arm(plan: dict, testbed: dict, schedules: Path, tokens: Path, admin_key: str, out: Path) -> dict | None:
    """The arm's run of a pair: the first valid attempt, attempting while there is none."""
    regime, k, arm = plan["regime"], plan["k"], plan["arm"]
    have = valid_run(out, regime, k, arm)
    if have:
        return have
    name = f"cell{plan['schedule']}"
    table = cs.read_table(HERE / "inputs" / f"{name}.csv.gz")
    for i in range(1, ATTEMPTS + 1):
        d = attempt_dir(out, regime, k, arm, i)
        if (d / "run.json").exists():
            continue
        try:
            rec = run_arm(plan, testbed, schedules / name, table, tokens, admin_key, d)
        except Exception as err:  # noqa: BLE001 (one run's trouble must not end the pilot)
            rec = {"arm": arm, "regime": regime, "k": k, "complete": True, "valid": False,
                   "problems": [f"{type(err).__name__}: {err}"[:400]]}
            d.mkdir(parents=True, exist_ok=True)
            (d / "run.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        print(f"{regime} pair {k} {arm} attempt {i}: {'valid' if rec['valid'] else 'INVALID: ' + '; '.join(rec['problems'])}",
              flush=True)
        if rec["valid"]:
            return rec
    return None


# -------------------------------------------------------------------- report

def sample_sizes(diffs: Sequence[float], comparator_mean: float, predicted_diff: float) -> dict:
    """As testbed/hvpa/pilot.sample_sizes: superiority at |predicted_diff|,
    equivalence within +-EQUIVALENCE of the comparator's mean."""
    sd = statistics.stdev(diffs) if len(diffs) > 1 else math.nan
    delta, eps = abs(predicted_diff), EQUIVALENCE * abs(comparator_mean)
    n_sup = ((Z_ALPHA + Z_POWER) * sd / delta) ** 2 if delta > 0 else math.inf
    n_eq = ((Z_ALPHA + Z_ALPHA) * sd / eps) ** 2 if eps > 0 else math.inf
    return {"pairs": len(diffs), "mean_diff": statistics.fmean(diffs) if diffs else math.nan, "sigma_d": sd,
            "predicted_diff": predicted_diff,
            "n_superiority": math.ceil(n_sup) if math.isfinite(n_sup) else None,
            "n_equivalence": math.ceil(n_eq) if math.isfinite(n_eq) else None}


def report(plans: dict, runs: dict, left_out: list[str]) -> tuple[dict, str]:
    out, lines = {}, ["# WP12 live pilot", "",
                      "Generated by `testbed/wp12/pairs.py` from the plans committed in `testbed/wp12/plans.json` "
                      f"(replica scaling `{plans['rules']['scaling']}`). Differences are per_knob minus joint; "
                      "pairs needed at alpha 0.05, power 0.9: superiority at the planners' own predicted mean "
                      f"difference, equivalence within +-{EQUIVALENCE:.0%} of the joint arm's mean. "
                      f"G3 asks for at most {G3_MAX_PAIRS} pairs per regime.", "",
                      "| regime | joint mean index | metric | pairs | joint mean | mean diff | sigma_d | predicted diff "
                      "| n superiority | n equivalence |", "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for regime, s in plans["summary"].items():
        out[regime] = {}
        pairs = sorted(k for (r, k) in runs if r == regime)
        for m in METRICS:
            diffs = [runs[(regime, k)]["per_knob"][m] - runs[(regime, k)]["joint"][m] for k in pairs]
            joint_mean = statistics.fmean(runs[(regime, k)]["joint"][m] for k in pairs) if pairs else math.nan
            ss = sample_sizes(diffs, joint_mean, s["predicted_mean_diff"][m])
            out[regime][m] = {**ss, "joint_mean": joint_mean}
            lines.append(f"| {regime} | {s['joint_mean_index']:.4f} | {m} | {ss['pairs']} | {joint_mean:.5g} | "
                         f"{ss['mean_diff']:+.5g} | {ss['sigma_d']:.5g} | {ss['predicted_diff']:+.5g} | "
                         f"{ss['n_superiority']} | {ss['n_equivalence']} |")
    lines += ["", f"Pairs left out after {ATTEMPTS} invalid attempts of an arm: "
              + (", ".join(left_out) if left_out else "none") + "."]
    return out, "\n".join(lines) + "\n"


def sequence_of(plans: dict) -> list[tuple[str, int]]:
    return run_order(tuple(plans["summary"]), 1 + max(p["k"] for p in plans["plans"]))


def collect(plans: dict, out: Path) -> tuple[dict, list[str]]:
    """Every pair whose two arms have a valid run under `out` (live runs or
    the kept records), and the pairs left out."""
    runs, left_out = {}, []
    for regime, k in sequence_of(plans):
        got = {arm: valid_run(out, regime, k, arm) for arm in order(k)}
        if all(got.values()):
            runs[(regime, k)] = {arm: rec["measured"] for arm, rec in got.items()}
        else:
            left_out.append(f"{regime} pair {k}")
    return runs, left_out


def plans_commit(path: Path) -> str:
    return rc.git("log", "-1", "--format=%H", "--", path.resolve().relative_to(rc.REPO).as_posix()).stdout.strip()


def write_report(plans: dict, out: Path, commit: str, dest: Path = HERE) -> str:
    runs, left_out = collect(plans, out)
    summary, text = report(plans, runs, left_out)
    (dest / "pilot.json").write_text(json.dumps({"plans_commit": commit, "summary": summary,
                                                 "runs": {f"{r}/{k}": v for (r, k), v in runs.items()},
                                                 "left_out": left_out}, indent=1), encoding="utf-8")
    (dest / "PILOT.md").write_text(text, encoding="utf-8")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--schedules", type=Path)
    ap.add_argument("--tokens", type=Path)
    ap.add_argument("--admin-key-file")
    ap.add_argument("--out", type=Path, required=True, help="the runs (live) or the kept records (--report-only)")
    ap.add_argument("--plans", type=Path, default=PLANS)
    ap.add_argument("--dry-run", action="store_true", help="print the run order and stop")
    ap.add_argument("--report-only", action="store_true", help="PILOT.md and pilot.json from the runs under --out")
    args = ap.parse_args(argv)
    plans = json.loads(args.plans.read_text(encoding="utf-8"))
    by = {(p["regime"], p["k"], p["arm"]): p for p in plans["plans"]}
    if args.dry_run:
        print(" ".join(f"{r}/{k}:{'/'.join(order(k))}" for r, k in sequence_of(plans)))
        return 0
    if args.report_only:
        print(write_report(plans, args.out, plans_commit(args.plans)))
        return 0
    if not (args.schedules and args.tokens and args.admin_key_file):
        ap.error("live runs need --schedules, --tokens and --admin-key-file")
    commit = rc.predictions_committed(args.plans.resolve())
    admin_key = actuate.admin_key(args.admin_key_file)
    for regime, k in sequence_of(plans):
        for arm in order(k):
            rec = run_pair_arm({**by[(regime, k, arm)], "plans_commit": commit}, plans["testbed"], args.schedules,
                               args.tokens, admin_key, args.out)
            if rec is None:
                break
    print(write_report(plans, args.out, commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
