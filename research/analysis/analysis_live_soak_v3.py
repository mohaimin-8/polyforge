#!/usr/bin/env python3
"""PREREG_LIVE_SOAK_V3.md -> RESULTS_LIVE_SOAK_V3.md (WP14, attempt 5).

**This record reports an INVALID run — the fourth SK-H4 failure.** The sitting
delivered 34 minutes of load with ZERO failed requests and a client-side p95 of
12 ms, then a single stall pinned one tenant's VU pool and produced 310 dropped
iterations, which `check_k6_delivery` rejected.

PREREG_LIVE_SOAK_V3 §Outcome handling names this outcome in advance:

    SK-H4 FAILS again -- this would be the fourth invalid sitting. The finding
    then is not "try a sixth time": it is that a 24 h live run of this system
    is not achievable on this machine, reported as such, with the ladder
    results as evidence that the apparatus was rebuilt and still could not.

So this record stops the retry loop, and it does so without relaxing the
threshold — which `PREREG_LIVE_SOAK_V2` also committed to in advance.

What attempt 5 adds that no earlier sitting could: a **direct, quantitative
demonstration that the server-side latency metric cannot see the failure**.
Across all 35 minutes, including the minute the stall occurred, per-minute
`crud_p95_ms` stayed between 2.02 and 2.38 ms while the client observed
4,365 ms. Attempt 4 inferred that gap from a pinned load path; here the load
path is fair (sixteen replicas, verified) and the gap is still there, because
`latency_ms` times `burnCPU` and stops before the telemetry write.

    python analysis_live_soak_v3.py
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
# Attempt 5's evidence, moved aside when attempt 6 reused the live_soak_evidence
# path. Attempt 3's evidence was LOST that way earlier in this work package, so
# each sitting now gets its own directory and every generator points at the one
# it actually reports.
EV = REPO / "eval" / "results" / "live_soak_attempt5_evidence"
STAGE_B = REPO / "eval" / "results" / "soak_stage_b_evidence"
STAGE_C = REPO / "eval" / "results" / "soak_stage_c_evidence"

VU_LINE = re.compile(
    r"tenant_(t\d+).*?(\d+)/(\d+)\s+VUs\s+(?:(\d+)d)?(\d+)h(\d+)m([\d.]+)s/")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def k6_metrics(evidence: Path) -> dict:
    return read_json(evidence / "k6-summary.json")["metrics"]


def vu_timeline(evidence: Path) -> dict[int, tuple[int, int]]:
    """minute -> (max active VUs on any single tenant, that tenant's pool)."""
    raw = io.open(evidence / "k6-live.log", encoding="utf-8",
                  errors="replace", newline="").read()
    peak: dict[int, tuple[int, int]] = {}
    for line in raw.split("\n"):
        if "iters/s" not in line:
            continue
        m = VU_LINE.search(line)
        if not m:
            continue
        days = int(m.group(4) or 0)
        seconds = (days * 86400 + int(m.group(5)) * 3600
                   + int(m.group(6)) * 60 + float(m.group(7)))
        minute = int(seconds // 60)
        active, pool = int(m.group(2)), int(m.group(3))
        if minute not in peak or active > peak[minute][0]:
            peak[minute] = (active, pool)
    return peak


def observer_rows(evidence: Path) -> list[dict]:
    path = evidence / "observer.csv"
    if not path.exists():
        return []
    return [r for r in csv.DictReader(path.read_text(encoding="utf-8").splitlines())
            if r.get("pods_total", "").isdigit()]


def imax(rows: list[dict], key: str) -> int:
    vals = [int(r[key]) for r in rows if r.get(key, "").isdigit()]
    return max(vals) if vals else 0


def fine_buckets(evidence: Path) -> list[dict]:
    path = evidence / "eval-export-fine.json"
    if not path.exists():
        path = evidence / "eval-export.json"
    if not path.exists():
        return []
    return read_json(path).get("buckets", [])


def pct(value: float) -> str:
    return f"{value:.4%}"


def build() -> str:
    m = k6_metrics(EV)
    reqs = m["http_reqs"]["count"]
    failed = m["http_req_failed"].get("value", 0.0) or 0.0
    dropped = m["dropped_iterations"].get("count", 0) or 0
    dur = m["http_req_duration"]
    vus_peak = m["vus"]["max"]

    peak = vu_timeline(EV)
    minutes = max(peak) + 1 if peak else 0
    excursions = sorted((w, a, p) for w, (a, p) in peak.items() if a >= 40)
    quiet = sum(1 for a, _ in peak.values() if a < 40)

    obs = observer_rows(EV)
    restarts = imax(obs, "restarts_total")
    node_mem = imax(obs, "nodes_mem_pct")

    export = read_json(EV / "eval-export.json")
    buckets = fine_buckets(EV)
    bucket_p95 = [b["crud_p95_ms"] for b in buckets]
    worst = max(buckets, key=lambda b: b["crud_p95_ms"]) if buckets else None

    b_m = k6_metrics(STAGE_B)
    c_m = k6_metrics(STAGE_C)
    c_obs = observer_rows(STAGE_C)
    c_export = read_json(STAGE_C / "eval-export.json")

    L: list[str] = []
    add = L.append

    add("# Live CRUD-plane soak — attempt 5: the run is INVALID, "
        "and the retry loop stops (WP14)")
    add("")
    add("Generated by `analysis_live_soak_v3.py` from evidence committed under "
        "`eval/results/live_soak_attempt5_evidence/`. Pre-registration: "
        "`PREREG_LIVE_SOAK_V3.md`, committed and pushed before the run.")
    add("")
    add("")
    add("## Headline — SK-H4 FAILS, the fourth invalid sitting")
    add("")
    add(f"The sitting delivered **{reqs:,} requests** over **{minutes} minutes** "
        f"with **{pct(failed)} failed**, then was rejected by "
        "`cluster_backend.check_k6_delivery`:")
    add("")
    add("| gate | observed | threshold | verdict |")
    add("|---|---:|---:|---|")
    add(f"| `http_req_failed` | **{pct(failed)}** | < 1% | PASS |")
    add(f"| `dropped_iterations` | **{dropped}** | < 1 | **FAIL** |")
    add("")
    add("`PREREG_LIVE_SOAK_V3` §Outcome handling names this outcome in advance: "
        "a fourth invalid sitting makes *the inability to sustain a 24 h live "
        "run on this machine* the finding, rather than something to keep "
        "retrying past. **The threshold is not relaxed.** "
        "`PREREG_LIVE_SOAK_V2` committed to that too, before any of these "
        "numbers existed: \"if attempt 3 fails on a handful of drops, that is "
        "the honest finding, and the response is NOT to relax the threshold\".")
    add("")
    add("This is a different failure from attempt 4's, and the difference "
        "matters. Attempt 4 lost 4.567% of its requests to a load path that "
        "pinned all traffic onto one replica of sixteen. Attempt 5 lost "
        f"**none** — {pct(failed)} — and was invalidated by "
        f"{dropped} dropped iterations out of {reqs:,}, "
        f"{dropped / reqs:.4%} of the run.")
    add("")
    add("")
    add("## What the run looked like before it stopped")
    add("")
    add("| quantity | value |")
    add("|---|---:|")
    add(f"| requests delivered | {reqs:,} |")
    add(f"| failed requests | {pct(failed)} |")
    add(f"| client p95 (`http_req_duration`) | {dur['p(95)']:.2f} ms |")
    add(f"| client median | {dur['med']:.2f} ms |")
    add(f"| client max | **{dur['max']:.0f} ms** |")
    add(f"| peak VUs | {vus_peak} |")
    add(f"| pod restarts (observer) | **{restarts}** |")
    add(f"| peak node memory (observer) | {node_mem}% |")
    add("")
    add(f"Of {minutes} minutes observed, **{quiet} were quiet** "
        "(under 40 VUs on every tenant). The run ended on a single excursion:")
    add("")
    add("| minute | peak VUs / pool | note |")
    add("|---|---:|---|")
    for w, a, p in excursions:
        add(f"| T+{w}m | **{a}/{p}** | pool exhausted |")
    add("")
    add("One tenant's arrival rate outran its VU pool for less than a minute, "
        "k6 could not start iterations on time, and the cumulative "
        "`dropped_iterations` counter crossed its absolute threshold. The "
        "counter never decreases, so a sub-minute event ends a 24-hour run "
        "permanently.")
    add("")
    add("")
    add("## The result attempt 5 adds: the server-side metric is blind to it")
    add("")
    add("Attempt 4 recorded a 464x gap between server-side and client-side "
        "latency and attributed it to a pinned load path. Attempt 5 has a "
        "**fair** load path — sixteen replicas, verified by "
        "`check_load_distribution` throughout the validation ladder — and the "
        "gap is still there.")
    add("")
    add("| measured at | value | what it includes |")
    add("|---|---:|---|")
    add(f"| server (`crud_p95_ms`, whole run) | {export['crud_p95_ms']:.3f} ms | "
        "the `burnCPU` spin only |")
    if bucket_p95:
        add(f"| server, worst single minute | **{max(bucket_p95):.3f} ms** | "
            f"of {len(buckets)} one-minute buckets |")
    add(f"| client (`http_req_duration` p95) | {dur['p(95)']:.2f} ms | "
        "the whole request |")
    add(f"| client, worst request | **{dur['max']:.0f} ms** | "
        "the whole request |")
    add("")
    if worst is not None:
        add(f"Per-minute server-side `crud_p95_ms` ranged "
            f"**{min(bucket_p95):.3f}–{max(bucket_p95):.3f} ms** across every "
            "minute of the run, *including the minute the stall happened*. The "
            "worst server-side minute was "
            f"{worst['bucket_start_utc']} at {worst['crud_p95_ms']:.3f} ms on "
            f"{worst['n_events']:,} events.")
    add("")
    add("`internal/platform/replay.go` starts its clock immediately before "
        "`burnCPU` and stops immediately after. Queueing, the telemetry write "
        "and the network are outside that window, so a stall in any of them is "
        "invisible to `crud_p95_ms` by construction. "
        "`internal/platform/replay_latency_semantics_test.go` pins the property "
        "directly: 250 ms injected outside the burn, 1 ms recorded.")
    add("")
    add("**Consequence for the committed record.** Every `crud_p95`/`crud_p99` "
        "in the live records measures CPU service time, not service latency. "
        "This was disclosed in the pre-registration before the run rather than "
        "discovered in scoring, and it is repeated here because the number "
        "reads like latency and is not.")
    add("")
    add(f"The whole-run `crud_p99` was **{export['crud_p99_ms']:.4f} ms** "
        "against the committed B2 baseline of **8.0072 ms** — the fourth "
        "independent corroboration in this work package that the live path "
        "measures what B2 measured.")
    add("")
    add("")
    add("## Hypotheses")
    add("")
    add("A hypothesis whose fault never fired is **UNSCOREABLE**, never a pass.")
    add("")
    add("| hypothesis | verdict | why |")
    add("|---|---|---|")
    add("| SK-H1 recovery | **UNSCOREABLE** | first injection is T+2 h; the "
        "run ended at T+34m |")
    add("| SK-H2 audit continuity | **UNSCOREABLE** | same — no planner outage "
        "occurred |")
    add("| SK-H3 latency stability | **UNSCOREABLE** | 35 minutes is not the "
        "24 hourly buckets the rule is stated over. For the record, every "
        f"one-minute bucket was below {max(bucket_p95):.3f} ms against the "
        "8.0072 ms threshold — reported, not scored |")
    add(f"| SK-H4 validity | **FAIL** | {dropped} dropped iterations |")
    add("| SK-H6 load distribution | **UNSCOREABLE** | the sampler summary was "
        "not preserved on an aborted run |")
    add(f"| SK-H7 client latency (descriptive) | reported | p95 "
        f"{dur['p(95)']:.2f} ms, median {dur['med']:.2f} ms, max "
        f"{dur['max']:.0f} ms |")
    add("")
    add("")
    add("## The validation ladder, which did pass")
    add("")
    add("The apparatus was rebuilt and validated on ascending durations before "
        "this sitting. Those runs are preconditions, not hypotheses, and they "
        "are the substantive live evidence this work package produced.")
    add("")
    add("| stage | duration | requests | failed | dropped | restarts |")
    add("|---|---:|---:|---:|---:|---:|")
    add(f"| B | 60 min | {b_m['http_reqs']['count']:,} | "
        f"{pct(b_m['http_req_failed'].get('value', 0) or 0)} | "
        f"{b_m['dropped_iterations'].get('count', 0)} | 0 |")
    add(f"| C | 4 h | {c_m['http_reqs']['count']:,} | "
        f"{pct(c_m['http_req_failed'].get('value', 0) or 0)} | "
        f"{c_m['dropped_iterations'].get('count', 0)} | "
        f"{imax(c_obs, 'restarts_total')} |")
    add("")
    add("Stage B also observed both repaired instruments **failing on demand**, "
        "which is what makes them instruments: SK-H1's `crud_p95` moved "
        "+189.7% under CPU starvation against a 20% band, and SK-H2's audit "
        "stream grew +72 healthy / +80 degraded in both windows.")
    add("")
    add("Stage C is the strongest live result here: "
        f"**{c_m['http_reqs']['count']:,} requests over four hours, "
        f"{pct(c_m['http_req_failed'].get('value', 0) or 0)} failed, "
        f"{c_m['dropped_iterations'].get('count', 0)} dropped, "
        f"{imax(c_obs, 'restarts_total')} restarts**, peak per-pod RSS 15 Mi "
        "against a 256 Mi limit, and hourly `crud_p95` of "
        + ", ".join(f"{b['crud_p95_ms']:.3f}" for b in c_export.get("buckets", []))
        + " ms — flat, slightly decreasing.")
    add("")
    add("")
    add("## What is and is not established")
    add("")
    add("**Established.** The mechanism that invalidated attempts 3 and 4 is "
        "gone: `kubectl port-forward` pinning all load onto one replica is "
        "replaced by a NodePort, and load distribution is now gated on every "
        "run. A second, independent blocker was found and fixed that no "
        "post-mortem had identified — `eval-export` could not have exported a "
        "24 h run at all, OOM-killing the pod at ~952k events against a "
        "sitting's ~25M. Attempt 4 was unwinnable for two reasons, not one.")
    add("")
    add("**Established.** PolyForge sustained four hours of continuous load at "
        "0.0000% failures with zero restarts and flat memory, and 34 minutes "
        "of a 24 h attempt at the same quality.")
    add("")
    add("**NOT established.** That a 24 h zero-drop run is achievable on this "
        "machine. An intermittent sub-minute stall — cause still "
        "unidentified — recurs often enough to make the absolute "
        "`dropped_iterations` threshold nearly impossible to survive for a "
        "day. `synchronous_commit=off` and longer checkpoint intervals reduced "
        "its frequency (two stalls in three hours before; one in roughly five "
        "and a half hours after) but did not remove it.")
    add("")
    add("**NOT established.** That the stall is a property of PolyForge. Every "
        "observation points away from the controller: pods never restarted, "
        "node memory stayed at "
        f"{node_mem}%, per-pod RSS was 15 Mi against a 256 Mi limit for four "
        "hours, and the server-side service time never moved. The eval store "
        "writes to an `emptyDir` on a VHDX under WSL2, which is the slowest "
        "component in the stack, and the control plane writes each telemetry "
        "event synchronously inside the request handler. That remains a "
        "hypothesis, not a result.")
    add("")
    add("")
    add("## Disposition")
    add("")
    add("The retry loop stops here, per the pre-registered stopping rule. A "
        "sixth sitting on this hardware is not the next step.")
    add("")
    add("What a future attempt would need, stated so the record is actionable "
        "rather than merely final:")
    add("")
    add("1. **Storage that does not stall.** The eval PostgreSQL on a real "
        "block device rather than an `emptyDir` over a VHDX, or a host whose "
        "I/O does not pause under fsync.")
    add("2. **Or a telemetry write path that does not block the handler.** "
        "Batched or asynchronous insertion would decouple request latency from "
        "storage latency. That is a product change and needs its own "
        "pre-registration, because it alters what the live plane measures.")
    add("3. **`PREREG_LIVE_SOAK_V3`'s registered fallback remains available** — "
        "\"if Docker cannot hold `medium` for the duration, drop to `small` and "
        "disclose; duration outranks width\" — but it is a fallback for a "
        "future prereg, not a way to keep this one running past its own "
        "stopping rule.")
    add("")
    add("Nothing in this record softens attempt 5's outcome. The sitting is "
        "INVALID, SK-H4 FAILED, and four of the six hypotheses could not be "
        "scored because the run ended before their faults fired.")
    add("")
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_LIVE_SOAK_V3.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
