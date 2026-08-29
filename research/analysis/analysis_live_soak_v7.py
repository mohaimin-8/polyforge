"""Attempt 9 (WP14) -> RESULTS_LIVE_SOAK_V7.md.

Pre-registration: `PREREG_LIVE_SOAK_V7.md`, committed and pushed at
2026-08-28T15:41:38Z, before the run's T0 of 15:46:09Z.

THIS RECORD REPORTS A VOID RUN, AND THAT IS ITS POINT. Attempt 9 was not a
failed sitting; it was a sitting whose system under test was altered mid-run by
a process no pre-registration describes. An unregistered second fault injector
applied four extra faults over 13 h 42 m. A record that presented its SK-H4
failure as a result would be attributing to PolyForge something the harness did
to itself.

What survives the contamination is the host-side evidence, and the reason it
survives is worth stating: `host_event_tail.ps1` reads the Windows System log.
It has no contact with the cluster, the injector or the workload, so the rogue
process could not affect what it recorded. The observer's `sample_gap_s` and
`host_write_ms` probes are likewise host-side. Those readings are reported
here; everything that depends on the cluster's behaviour is not.

Sources: `eval/results/live_soak_attempt9_evidence/` for the registered run and
`eval/results/live_soak_attempt8b_evidence/`, which the rogue launcher
overwrote and which is therefore the rogue injector's own log.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EV = REPO / "eval" / "results" / "live_soak_attempt9_evidence"
ROGUE = REPO / "eval" / "results" / "live_soak_attempt8b_evidence"

GAP_THRESHOLD_S = 40      # SK-H8: 2x the observer's 20 s sampling interval
MATCH_WINDOW_S = 120      # SK-H8: a match is a named event within +/- this
SLOW_WRITE_MS = 900


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def num(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def timeline(path: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    p = path / "timeline.txt"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), parts[1]))
    return out


def load_t0(path: Path) -> int:
    for line in (path / "timeline.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("T0="):
            return int(line.split("=", 1)[1])
    raise SystemExit(f"{path}/timeline.txt has no T0")


def faults(events: list[tuple[int, str]], source: str) -> list[dict]:
    """Pair each *_down/*_on with its matching *_up/*_off."""
    out: list[dict] = []
    pending: dict[str, int] = {}
    for epoch, name in events:
        if name.endswith(("_down", "_on")):
            pending[name.rsplit("_", 1)[0]] = epoch
        else:
            kind = name.rsplit("_", 1)[0]
            if kind in pending:
                out.append({"kind": kind, "on": pending.pop(kind),
                            "off": epoch, "source": source})
    return out


def iso(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def hhmm(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600} h {(s % 3600) // 60:02d} m"


def iso_epoch(row: dict) -> int:
    """The sample's `iso` column as an epoch, to compare with its own `epoch`.

    The observer takes the two with separate `date` calls, so a divergence
    measures how long that second process spawn took -- an accidental
    sub-sample contention probe, and the only instrument that registered the
    fatal stall at all.
    """
    return int(dt.datetime.strptime(row["iso"], "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=dt.timezone.utc).timestamp())


def scored_window(obs: list[dict], t0: int) -> list[dict]:
    """T0 to the last sample that still saw a pod.

    The observer kept sampling for over an hour after the harness deleted the
    cluster. Those rows are not the sitting and are excluded.
    """
    rows = [r for r in obs if int(r["epoch"]) >= t0]
    last = 0
    for i, r in enumerate(rows):
        if num(r, "pods_total") > 0:
            last = i
    return rows[:last + 1]


def build() -> str:
    L: list[str] = []
    def w(s: str = "") -> None:
        L.append(s)

    k6 = json.loads((EV / "k6-summary.json").read_text(encoding="utf-8"))
    m = k6["metrics"]
    reqs = int(m["http_reqs"]["count"])
    duration_s = reqs / float(m["http_reqs"]["rate"])
    failed_n = int(m["http_req_failed"]["passes"])
    failed_pct = float(m["http_req_failed"]["value"]) * 100.0
    dropped = int(m["dropped_iterations"]["count"])
    p95 = float(m["http_req_duration"]["p(95)"])
    med = float(m["http_req_duration"]["med"])
    worst = float(m["http_req_duration"]["max"])
    vus_pool = int(m["vus_max"]["value"])
    vus_peak = int(m["vus"]["max"])

    t0 = load_t0(EV)
    obs = read_rows(EV / "observer.csv")
    scored = scored_window(obs, t0)
    restarts = max((num(r, "restarts_total") for r in scored), default=0)
    peak_mem = max((num(r, "nodes_mem_pct") for r in scored), default=0)
    rows_ingested = max((num(r, "pg_rows_ingested") for r in scored), default=0)
    max_write = max((num(r, "host_write_ms") for r in scored), default=0)
    slow_writes = [r for r in scored if num(r, "host_write_ms") > SLOW_WRITE_MS]
    excursions = [r for r in scored if num(r, "sample_gap_s") > GAP_THRESHOLD_S]

    evts = sorted(read_rows(EV / "host_events.csv"),
                  key=lambda r: int(r["epoch"]))
    power = [e for e in evts
             if "Kernel-Power" in e["provider"] and e["event_id"] == "105"]
    storage = [e for e in evts if e["provider"].lower() in
               {"volsnap", "vss", "disk", "ntfs", "volmgr",
                "storahci", "stornvme"}]

    registered = faults(timeline(EV), "registered")
    rogue = faults(timeline(ROGUE), "**UNREGISTERED**")
    all_faults = sorted(registered + rogue, key=lambda f: f["on"])

    matches = [(r, [e for e in evts
                    if abs(int(e["epoch"]) - int(r["epoch"])) <= MATCH_WINDOW_S])
               for r in excursions]
    matched_n = sum(1 for _, near in matches if near)

    # ---------------------------------------------------------------- head --
    w("# Live CRUD-plane soak — attempt 9: VOID, and what survives it (WP14)")
    w()
    w("Generated by `analysis_live_soak_v7.py` from evidence committed under "
      "`eval/results/live_soak_attempt9_evidence/` and "
      "`eval/results/live_soak_attempt8b_evidence/`. Pre-registration: "
      "`PREREG_LIVE_SOAK_V7.md`, committed and pushed at "
      "2026-08-28T15:41:38Z — before the run's T0 of 2026-08-28T15:46:09Z.")
    w()
    w()
    w("## Headline — the run is VOID, and its SK-H4 failure is NOT a result")
    w()
    w(f"The sitting ran **{hhmm(duration_s)}**, delivered **{reqs:,} requests** "
      f"at **{failed_pct:.4f}% failed**, and stopped on **{dropped:,} dropped "
      "iterations**.")
    w()
    w("**None of that can be attributed to the system under test.** From "
      "2026-08-28T17:59:03Z, 2 h 13 m after T0, an **unregistered second fault "
      "injector** ran against the same cluster and kept running for "
      "**13 h 42 m**. The cluster received "
      f"**{len(all_faults)} fault injections instead of {len(registered)}**.")
    w()
    w("A run whose system under test was altered by a process no "
      "pre-registration describes is **VOID, not failed**. The roadmap's own "
      "contingency is explicit — *\"Run VOID. Disclose, re-sit\"* — and "
      "reporting the SK-H4 failure as a property of PolyForge would attribute "
      "to the system something the harness did to itself.")
    w()
    w()
    w("## The contamination, in full")
    w()
    w("| time (UTC) | fault | duration | source |")
    w("|---|---|---:|---|")
    for f in all_faults:
        w(f"| {iso(f['on'])} | {f['kind']} | {f['off'] - f['on']} s | "
          f"{f['source']} |")
    w()
    w("**No two injections overlapped**, which is the one mercy: the faults "
      "interleaved rather than colliding, so each was individually a clean "
      "injection. The sitting as a whole was still not the registered "
      "experiment.")
    w()
    w("**Cause, fully traced.** The abandoned attempt-8b launcher "
      "(`launch_soak8.sh`, retained as `.DISABLED`) was executed from "
      "PowerShell at 17:59:03Z. It opens with "
      "`rm -f .observer.lock .controls.lock`, deleting the single-instance "
      "guard outright; backgrounds a second `soak_observer.sh` and a second "
      "`stage_b_controls.sh` carrying the full 24 h schedule; then starts a "
      "harness runner that died six seconds later on a DuckDB file lock held "
      "by the live run:")
    w()
    w("```")
    w("_duckdb.IOException: ...live_soak.duckdb: used by another process")
    w("File is already open in python.exe (PID 628)")
    w("```")
    w()
    w("**The runner died. Its backgrounded children did not**, because that "
      "launcher has no trap. Two defects, each survivable alone: the lock "
      "would have stopped this had the launcher not deleted it, and the dead "
      "runner would have been harmless had it cleaned up after itself. "
      "`live_soak_attempt8b_evidence/timeline.txt` carries the two `T0=` lines "
      "that are this failure's documented signature.")
    w()
    w("**Instrumentation load was doubled** for 13 h 42 m: a second observer "
      "sampling the same cluster every 20 s, and a second Windows event tail. "
      "That is a standing confound for anything read off cluster behaviour.")
    w()
    w()
    w("## What cannot be claimed")
    w()
    w("| claim | status |")
    w("|---|---|")
    w("| SK-H4 validity failure | **not attributable** — nine faults, not "
      f"{len(registered)} |")
    w("| SK-H1 / SK-H2 / SK-H3 / SK-H6 | **UNSCOREABLE** — `eval-export` never "
      "ran; the delivery gate rejected the run before the export step |")
    w("| duration or throughput as a sitting result | **no** — the run was not "
      "governed by its prereg |")
    w("| the 12 h minimum as cleared | **no** — for the same reason |")
    w()
    w("Four hypotheses were unscoreable independently of the contamination, "
      "for the same reason attempt 8's were: no 60 s or 3600 s buckets exist. "
      "No substitute source was used to manufacture a verdict the "
      "pre-registration did not register.")
    w()
    w("What the numbers do describe — as machine behaviour, not as a scored "
      "result — is that the box sustained "
      f"{reqs:,} requests over {hhmm(duration_s)} at {failed_pct:.4f}% failed "
      f"with **{int(restarts)} pod restarts** and peak node memory "
      f"{int(peak_mem)}%, through **{len(all_faults)}** fault injections. "
      f"Client p95 {p95:.2f} ms, median {med:.2f} ms, worst "
      f"{worst / 1000:.2f} s. Telemetry ingested {int(rows_ingested):,} rows. "
      f"The VU pool held {vus_pool:,} and peaked at {vus_peak:,}.")
    w()
    w()
    w("## What survives — the host-side record")
    w()
    w("**The rogue injector could not touch this evidence.** "
      "`host_event_tail.ps1` reads the Windows System log; `sample_gap_s` and "
      "`host_write_ms` time a scheduling loop and a volume write. None of the "
      "three has any contact with the cluster, the injector or the workload.")
    w()
    w(f"Across {hhmm(duration_s)} the tail recorded **{len(evts)} events**:")
    w()
    w("| iso_utc | provider | id | level | message |")
    w("|---|---|---:|---|---|")
    for e in evts:
        w(f"| {e['iso_utc']} | {e['provider']} | {e['event_id']} | "
          f"{e['level']} | {e['message']} |")
    w()
    w(f"**{len(power)} are `Kernel-Power` \"Power source change\". "
      f"{len(storage)} are storage events of any kind.** Volsnap event 36 — "
      "the circumstantial evidence behind attempts 7 and 8 — did not occur "
      "once. Attempt 7 had already falsified the storage-durability "
      "hypothesis by disabling `fsync`, `full_page_writes` and "
      "`synchronous_commit` together and watching the stall grow; this adds "
      "the direct observation that the storage subsystems said nothing at all.")
    w()
    w("### SK-H8 (descriptive, non-gating)")
    w()
    w("Declared before any data: *every `sample_gap_s` excursion above "
      f"{GAP_THRESHOLD_S} s coincides, within +/-{MATCH_WINDOW_S} s, with a "
      "named host event*. It gates nothing.")
    w()
    w(f"**Result: {matched_n} of {len(excursions)} excursions matched.**")
    w()
    w("| excursion | gap | host write | pods | matched event(s) |")
    w("|---|---:|---:|---:|---|")
    for r, near in matches:
        names = ", ".join(f"{e['provider']} {e['event_id']} @ {e['iso_utc']}"
                          for e in near) or "**none**"
        w(f"| {r['iso']} | {int(num(r, 'sample_gap_s'))} s | "
          f"{int(num(r, 'host_write_ms'))} ms | "
          f"{int(num(r, 'pods_ready'))}/{int(num(r, 'pods_total'))} | {names} |")
    w()
    w("Both matched excursions belong to the **first** power episode, and "
      "neither is the one that ended the run.")
    w()
    w(f"The {len(slow_writes)} writes above {SLOW_WRITE_MS} ms "
      f"(peak {int(max_write)} ms) each occurred with a **normal** sampling "
      "gap and cost nothing:")
    w()
    w("| iso | host write | sample gap |")
    w("|---|---:|---:|")
    for r in slow_writes:
        w(f"| {r['iso']} | {int(num(r, 'host_write_ms'))} ms | "
          f"{int(num(r, 'sample_gap_s'))} s |")
    w()
    w("Slow volume writes and host scheduling stalls are separable on this "
      "machine.")
    w()
    w()
    w("## The fatal window — and the instrument that missed it")
    w()
    w("The run ended at 06:28:17Z. A power-source change at 06:27:12Z precedes "
      "it by sixty-five seconds, but **`sample_gap_s` did not register the "
      "fatal stall at all** — the observer's loop was scheduled normally "
      "through it:")
    w()
    w("| iso | sample gap | host write | pods ready | node mem | "
      "`iso` − `epoch` |")
    w("|---|---:|---:|---:|---:|---:|")
    for r in scored[-3:]:
        w(f"| {r['iso']} | {int(num(r, 'sample_gap_s'))} s | "
          f"{int(num(r, 'host_write_ms'))} ms | "
          f"{int(num(r, 'pods_ready'))}/{int(num(r, 'pods_total'))} | "
          f"{int(num(r, 'nodes_mem_pct'))}% | "
          f"{iso_epoch(r) - int(r['epoch']):+d} s |")
    w()
    w("The two episodes have **different signatures and must not be merged**. "
      "In the first, the observer process itself was descheduled for 48 s "
      "twice — a Windows-side stall. In the second, the observer ran on time "
      "while the cluster stopped answering: `kubectl top` returned nothing "
      "(the 0% is a failed sample, not a reading), two pods left Ready, and "
      "k6's iterations froze. `k6`'s progress log shows active VUs — 2 to 13 "
      "all sitting — climb 113 → 754 → 1,400 → 2,194 → 3,338 in 32 seconds "
      "while completed iterations advanced by **three**.")
    w()
    w("The only sub-sample evidence of Windows-side contention is the last "
      "column: the observer's two adjacent `date` calls, normally agreeing to "
      "the second, diverge by **6 s** in the fatal sample. A real signal, and "
      "a weak one — an artefact read after the fact, not a designed "
      "instrument.")
    w()
    w("The honest reading is narrower than \"the host froze\": **the guest — "
      "the WSL2 VM hosting the cluster — stalled while the Windows host kept "
      "scheduling.** Whether the two episodes share one mechanism is not "
      "established by two observations, and this record does not assert it. "
      "For whoever instruments the next sitting: **`sample_gap_s` is blind to "
      "a guest-only stall**, and a probe inside the VM or a k6 iteration-rate "
      "watchdog would have caught what this one missed.")
    w()
    w()
    w("## Limitations")
    w()
    w("**The contamination is the first limitation and it dominates the "
      "rest.** Nothing here about cluster behaviour, latency, recovery or "
      "audit continuity should be carried forward.")
    w()
    w("**The tail watches the System log only.** Defender scans, Search "
      "indexing and scheduled maintenance write to Application and Operational "
      "channels this instrument does not read. \"No storage event\" means "
      "*not Volsnap, not the disk stack, not the filesystem drivers* — not "
      "\"the host was idle\".")
    w()
    w("**Two episodes is not a mechanism study.** The association between the "
      "power transitions and the stalls is tight — six seconds for the fatal "
      "one — and it is the only named activity in the log. It is not a "
      "controlled demonstration.")
    w()
    w("**`crud_p95` / `crud_p99` measure CPU service time, not service "
      "latency**, carried forward unchanged from V5: "
      "`internal/platform/replay.go` stops its clock before the telemetry "
      "write, pinned by `internal/platform/replay_latency_semantics_test.go`.")
    w()
    w()
    w("## What follows")
    w()
    w("`PREREG_LIVE_SOAK_V8.md` registers attempt 10 as the first sitting V7's "
      "apparatus actually governs, with one changed factor: a liveness "
      "heartbeat the offending launcher cannot delete, tested against this "
      "exact failure, plus a launcher whose trap kills its instruments on any "
      "exit. **The instruments must not survive the run they instrument.**")
    w()
    w("`RESULTS_LIVE_SOAK_V5.md`, `RESULTS_LIVE_SOAK_V3.md` and "
      "`RESULTS_LIVE_SOAK_V2.md` stand as committed. This record does not "
      "replace them and does not soften them.")
    w()
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path("RESULTS_LIVE_SOAK_V7.md")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
