#!/usr/bin/env python3
"""L5 — a real trace driving a real cluster (PREREG_TRACE_LIVE.md).

Every trace result this project had was a simulation, and every live experiment
drove a synthetic cell. This scores the first sitting where a real trace drove a
real cluster, against a simulator run on the *same* demand.

The live side is `eval/results/trace_live.duckdb`, two valid arms of 720 steps
each. The sim side is computed here from the identical buckets, via the same
`trace_matrix.window_buckets` projection the live harness used, so neither side
can drift from the other.

**The one deliberate difference from `trace_matrix.run_one`, and why.** That
function pins `limits=MEDIUM` (4096 MB, 48 replicas) because the published trace
matrix is a medium-cluster experiment. The live sitting ran `small` (2048 MB, 24
replicas). A paired test has to match substrates, so the limits here come from
the live run's own `cluster_size`. Everything else -- controller, tuned params,
seeding, LRU factor, weights -- is taken from the same `harness.systems`
registry `run_one` reads, and `trace_matrix.py` is not modified, so
`RESULTS_TRACE_PARITY.md` and `RESULTS_BUDGET_PARITY.md` replay byte-identically.

Hypotheses are TL-H1..TL-H4 as frozen in the pre-registration. This script
decides them; it does not describe a decision made by reading the numbers.

    python analysis_trace_live.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import json

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "research" / "analysis"))
sys.path.insert(0, str(REPO_ROOT / "research" / "jcac_sim"))
sys.path.insert(0, str(REPO_ROOT / "eval"))

import trace_matrix as tm  # noqa: E402
import simulate  # noqa: E402
from controller import ClusterLimits, Weights  # noqa: E402
from harness import trace_demand as td  # noqa: E402
from harness import workloads as W  # noqa: E402
from harness.systems import SYSTEMS, lru_miss_cost_factor, tuned_params  # noqa: E402
from stats import record_path  # noqa: E402

LIVE_DB = REPO_ROOT / "eval" / "results" / "trace_live.duckdb"
EVIDENCE = REPO_ROOT / "eval" / "results" / "trace_live_evidence"
# `eval/results/*.duckdb` is gitignored, so the committed CSV export is what
# makes this record rebuild on a clean clone -- the same fallback every other
# campaign record uses.
LIVE_CSV = REPO_ROOT / "eval" / "results" / "trace_live_runs.csv"
RECORD = "RESULTS_TRACE_LIVE.md"
ARMS = ("jcac", "hpa")
WINDOW = 0
MAX_FAILURE_RATE = 0.01      # TL-H3, k6's own threshold
VOLUME_TOLERANCE = 0.05      # TL-H3, encoded arrival volume vs the projection


def live_rows() -> dict[str, dict]:
    if not LIVE_DB.exists():
        return live_rows_from_csv()
    con = duckdb.connect(str(LIVE_DB), read_only=True)
    cols = [c[0] for c in con.execute(
        "select column_name from information_schema.columns "
        "where table_name='metrics'").fetchall()]
    runs = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "select run_id, system, status, cluster_size from runs").fetchall()}
    out = {}
    for row in con.execute("select * from metrics").fetchall():
        d = dict(zip(cols, row))
        system, status, size = runs.get(d.get("run_id"), ("?", "?", "small"))
        if status == "valid":
            d["cluster_size"] = size
            out[system] = d
    con.close()
    return out


def live_rows_from_csv() -> dict[str, dict]:
    """Clean-clone path: the committed export, not the gitignored DuckDB."""
    import csv

    if not LIVE_CSV.exists():
        return {}
    numeric = ("steps", "total_cost_usd", "mean_violation", "mean_jain",
               "cache_hit_rate", "ai_p95_ms", "crud_p95_ms")
    out = {}
    with LIVE_CSV.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") != "valid":
                continue
            for key in numeric:
                if row.get(key) not in (None, ""):
                    row[key] = float(row[key])
            row["steps"] = int(row["steps"])
            out[row["system"]] = row
    return out


def sim_row(system: str, buckets, size_name: str) -> dict:
    """`trace_matrix.run_one`'s construction, at the LIVE run's cluster limits."""
    size = W.CLUSTER_SIZES[size_name]
    limits = ClusterLimits(cache_mb=size.limits_cache_mb,
                           replicas=size.limits_replicas)
    spec = SYSTEMS[system]
    tenant_ids = [f"t{i:02d}" for i in range(tm.TENANTS)]
    configs = simulate.default_configs(tenant_ids)
    params = dict(tuned_params().get(spec.controller, {}))
    params.update(spec.params)
    if spec.seeded:
        params["seed"] = tm.SEED_BASE + WINDOW
    evict_overhead_us = params.pop("evict_overhead_us", None)
    weight_kw = {k: v for k, v in (("gamma", spec.gamma),
                                   ("beta", getattr(spec, "beta", None)))
                 if v is not None}
    result = simulate.run(
        spec.controller, tenant_ids, buckets,
        configs=configs, weights=Weights(**weight_kw) if weight_kw else None,
        limits=limits, collect_rows=False, controller_params=params or None,
        miss_cost_factor=lru_miss_cost_factor() if spec.lru_eviction else 1.0,
        initial_cache_mb=spec.static_cache_mb,
        evict_overhead_ms=(evict_overhead_us / 1000.0) if evict_overhead_us else None,
    )
    return {
        "total_cost_usd": result.total_cost_usd,
        "mean_violation": result.mean_violation,
        "mean_jain": result.mean_jain,
        "cache_hit_rate": result.cache_hit_rate,
        "steps": result.steps,
    }


def delivery(buckets, interval_s: int) -> dict:
    """TL-H3: did k6 deliver the registered demand?

    NOTE, and it bounds the check: the evidence directory is REUSED per run
    (the same convention as `live_soak_evidence`), so only the final arm's k6
    summary survives. TL-H3 is therefore evidenced for that arm alone, and this
    record says so rather than implying both were checked.
    """
    path = EVIDENCE / "k6-summary.json"
    if not path.exists():
        return {"evaluable": False, "reason": f"missing {path.name}"}
    metrics = json.loads(path.read_text(encoding="utf-8")).get("metrics", {})
    failed = metrics.get("http_req_failed", {}).get("value")
    reqs = metrics.get("http_reqs", {}).get("count")
    dropped = metrics.get("dropped_iterations", {}).get("count")
    if failed is None or reqs is None:
        return {"evaluable": False, "reason": "k6 summary lacks the delivery metrics"}
    expected = sum(sum(d.total_rps() for d in bk.values())
                   for bk in buckets) * interval_s
    ratio = (reqs / expected) if expected else float("inf")
    ok = (failed <= MAX_FAILURE_RATE
          and abs(ratio - 1.0) <= VOLUME_TOLERANCE
          and (dropped or 0) == 0)
    return {"evaluable": True, "failure_rate": failed, "requests": reqs,
            "expected": expected, "ratio": ratio, "dropped": dropped, "ok": ok}


def order(a: float, b: float, eps: float = 1e-12) -> str:
    """Ordering of arm A against arm B: '<', '>' or '='."""
    if abs(a - b) <= eps:
        return "="
    return "<" if a < b else ">"


def main() -> int:
    live = live_rows()
    L: list[str] = []
    w = L.append
    w("# A real trace driving a real cluster (L5)\n")
    w("Generated by `analysis_trace_live.py`. Pre-registration: "
      "`PREREG_TRACE_LIVE.md` (+ Amendment 1 arms, Amendment 2 sitting-1 "
      "VOID), committed and pushed before the campaign ran.\n")

    missing = [a for a in ARMS if a not in live]
    if missing:
        w(f"**Not scoreable: no valid live run for {missing}.** "
          "The sitting did not complete.\n")
        record_path(RECORD).write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"missing arms: {missing}")
        return 1

    size_name = live[ARMS[0]].get("cluster_size", "small")
    steps = int(live[ARMS[0]]["steps"])
    tenant_ids, buckets, meta = td.trace_window(WINDOW)
    buckets = buckets[:steps + 1]

    w("\n## What ran\n")
    w("| | |")
    w("|---|---|")
    w(f"| trace | `{Path(meta['trace']).name}` |")
    w(f"| window | {meta['window_index']}, start_s {meta['window_start_s']}, "
      f"scale factor {meta['scale_factor']:.6f} |")
    w(f"| scored slice | {steps} steps x {meta['control_interval_s']} s "
      f"= {steps * meta['control_interval_s'] / 3600:.2f} h per arm |")
    w(f"| demand kinds | {', '.join(meta['kinds'])} (BurstGPT records LLM "
      "arrivals only) |")
    w(f"| peak aggregate arrival | {td.peak_total_rps(buckets):.3f} rps |")
    w(f"| tenants | {meta['tenants']} |")
    w(f"| cluster | kind, `{size_name}` |")
    w("| tier backends | `kaggle_tier_server.py --mock`, delays from "
      "`TIER_BENCH.md` |")
    w("")
    w("**The honest departure, restated from the prereg:** tier latency is "
      "*injected* from the committed bench, not generated by a model. Real "
      "here are the control loop, the gateway, the semantic cache, replica "
      "actuation, PostgreSQL, the arrival process and the trace. Absolutes are "
      "therefore not comparable between substrates, which is why only the "
      "*ordering* is under test.\n")

    sim = {arm: sim_row(arm, buckets, size_name) for arm in ARMS}

    w("\n## Measured\n")
    w("| metric | arm | live | sim |")
    w("|---|---|---:|---:|")
    for metric in ("total_cost_usd", "mean_violation", "mean_jain", "cache_hit_rate"):
        for arm in ARMS:
            w(f"| `{metric}` | `{arm}` | {live[arm][metric]:.6g} | {sim[arm][metric]:.6g} |")
    w("")

    a, b = ARMS
    results = {}
    for tag, metric in (("TL-H1", "total_cost_usd"), ("TL-H2", "mean_violation")):
        live_order = order(live[a][metric], live[b][metric])
        sim_order = order(sim[a][metric], sim[b][metric])
        agree = live_order == sim_order
        # An "=" on BOTH sides is not evidence that an ordering transferred:
        # there was no ordering to transfer. S2's first permutation check
        # passed exactly this way, on a cell where contention was impossible,
        # and the project's rule since is that a check which cannot fail is
        # not a check. Reported as VACUOUS, never as PASS.
        vacuous = agree and live_order == "=" and sim_order == "="
        results[tag] = (metric, live_order, sim_order, agree, vacuous)

    w("\n## TL-H1 / TL-H2 — does the live ordering match the simulator's?\n")
    w(f"| id | metric | live | sim | agree? |")
    w("|---|---|---|---|---|")
    for tag, (metric, lo, so, ok, vac) in results.items():
        mark = "**VACUOUS**" if vac else ("**yes**" if ok else "**NO**")
        w(f"| **{tag}** | `{metric}` | `{a}` {lo} `{b}` | `{a}` {so} `{b}` | {mark} |")
    w("")

    for tag, (metric, lo, so, ok, vac) in results.items():
        verdict = "VACUOUS" if vac else ("PASS" if ok else "FAIL")
        w(f"**{tag} {verdict}** — on `{metric}`, live ranks `{a}` {lo} `{b}` "
          f"and the simulator ranks it `{a}` {so} `{b}`.")
        if vac:
            w(f"\n**This is not a PASS.** Both substrates report "
              f"`{metric}` identical across the two arms, so there was no "
              "ordering to transfer and the comparison could not have failed. "
              "At this cell's arrival rate the metric simply does not "
              "discriminate. Recorded as VACUOUS, and it supports no claim.\n")
        elif not ok:
            w(f"\nPer the falsifier frozen before the run, a disagreement IS "
              "the result and is not re-run, re-tuned or widened. It is direct "
              "evidence that a sim-only ordering does not transfer to a real "
              "cluster on this cell — the objection this project has otherwise "
              "been conceding in prose.\n")
        else:
            w("")

    w("\n## TL-H3 — did the substrate deliver the registered demand? (gating)\n")
    deliv = delivery(buckets, meta["control_interval_s"])
    if not deliv["evaluable"]:
        w(f"**TL-H3 NOT EVALUABLE** — {deliv['reason']}. Per the prereg a "
          "failure here voids TL-H1/TL-H2 rather than softening them, and an "
          "unevaluable gate is not a passed gate.\n")
    else:
        w("| quantity | value | gate |")
        w("|---|---:|---|")
        w(f"| k6 failure rate | {deliv['failure_rate']:.4f} | "
          f"<= {MAX_FAILURE_RATE} |")
        w(f"| requests delivered | {deliv['requests']:,} | — |")
        w(f"| requests projected | {deliv['expected']:,.0f} | — |")
        w(f"| volume ratio | {deliv['ratio']:.3f} | within "
          f"{VOLUME_TOLERANCE:.0%} of 1.000 |")
        w(f"| dropped iterations | {deliv['dropped']} | 0 |")
        w("")
        w(f"**TL-H3 {'PASS' if deliv['ok'] else 'FAIL'}.** The volume overage "
          "is the 1/60 rps quantisation of `timeUnit: \"1m\"`, which is the "
          "resolution that made a sub-1-rps trace expressible at all.\n")
        w("> **Bound on this check:** the evidence directory is reused per run "
          "(the `live_soak_evidence` convention), so only the **final arm's** "
          "k6 summary survives. TL-H3 is evidenced for that arm alone. Both "
          "arms were recorded `valid` by the harness, whose own validity gate "
          "covers step count and telemetry, but the delivery numbers above "
          "describe one arm.\n")

    w("\n## TL-H4 — live/sim ratios (descriptive, no threshold)\n")
    w("| metric | arm | ratio live/sim |")
    w("|---|---|---:|")
    for metric in ("total_cost_usd", "cache_hit_rate"):
        for arm in ARMS:
            s = sim[arm][metric]
            w(f"| `{metric}` | `{arm}` | "
              f"{(live[arm][metric] / s) if s else float('nan'):.3f} |")
    w("")
    w("No threshold is attached and none was pre-registered: injected tier "
      "latency makes absolute agreement unclaimable. These are reported so the "
      "*size* of the substrate gap is visible rather than implied.\n")

    w("\n## What this does and does not license\n")
    strict = [(t, r) for t, r in results.items() if not r[4]]
    passes = bool(strict) and all(r[3] for _, r in strict)
    if passes:
        w(f"On real trace demand, the live cluster ranked `{a}` and `{b}` as "
          "the simulator did, on both scored metrics. That is the whole claim. "
          "It does **not** license 'the simulator is validated', does not "
          "transfer to arms that were not run, and does not make the injected "
          "tier latencies real.\n")
    else:
        w("At least one ordering did not transfer. The narrow reading is that "
          "on this cell, with this demand, a sim-derived ranking is not a safe "
          "guide to the live one — which is a limitation of the evaluation and "
          "is reported as one.\n")
    w("**W4 is closed either way**: the sentence *\"no real trace has ever "
      "driven a real cluster\"* is no longer true of this project. Two arms "
      f"ran {steps * meta['control_interval_s'] / 3600:.2f} h each on real "
      "BurstGPT demand, on a real cluster, scored against a pre-registration.\n")

    out = record_path(RECORD)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    for tag, (metric, lo, so, ok, vac) in results.items():
        print(f"{tag} {'VACUOUS' if vac else ('PASS' if ok else 'FAIL')} | "
              f"{metric} | live {a}{lo}{b} | sim {a}{so}{b}")
    print(f"TL-H3 {'PASS' if deliv.get('ok') else 'FAIL/NOT EVALUABLE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
