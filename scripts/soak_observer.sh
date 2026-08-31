#!/usr/bin/env bash
# Sample the cluster while a soak stage runs, so a stall has an explanation.
#
# WHY THIS EXISTS. Stage B aborted at t+3161s on 26 dropped iterations. The
# generator's pool was the proximate cause and is fixed (cluster_backend
# k6_script), but the pool only ran out because something in the system under
# test stalled for seconds — and NOTHING recorded what. The whole run left four
# files behind, none of which can say whether a pod restarted, whether
# PostgreSQL was checkpointing, or whether the WSL2 VM was reclaiming memory.
#
# That is the same hole that made attempts 1-4 diagnosable only in hindsight,
# and always after the next 24-hour run. Phase 2.3 of the remediation roadmap
# asks for the run to be observable WHILE it runs; this is the cluster half of
# it. (The roadmap also asks for k6 `--out csv`. Deliberately not used: it
# writes a row per metric per iteration, which is ~1 GB for this stage and
# ~25 GB for the 24 h sitting. The 60 s buckets eval-export now emits carry the
# same timeline server-side, at a size that survives.)
#
#   scripts/soak_observer.sh [interval_s] &
#
# Every sample is a CSV row; a sample that cannot be taken writes empty fields
# rather than stopping. This must never be the reason a run fails.

set -uo pipefail
NS=polyforge
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE="${EVIDENCE_DIR:-$REPO/eval/results/soak_stage_b_evidence}"
INTERVAL=${1:-20}
mkdir -p "$EVIDENCE" 2>/dev/null || true
OUT="$EVIDENCE/observer.csv"
# TRUE SERVICE LATENCY (W5). `internal/platform/replay.go` stops its clock
# before the telemetry write, so `LatencyMS` -- and every `crud_p95` derived
# from it -- is CPU service time, not service latency. The end-to-end number
# already exists: `server.go`'s middleware times the whole handler, telemetry
# write included, and exports it as
# `polyforge_http_request_duration_seconds{method,route}`. Nothing ever
# scraped it, so no sitting through attempt 10 can be re-read against it.
# This captures it from here on.
#
# Scraped over the NodePort the load already uses, NOT a port-forward: a
# port-forward is what pinned every request to one pod of sixteen in attempt 4.
# `/metrics` bypasses the rate limiter (server.go rateLimit), so the scrape
# cannot consume a tenant's budget.
NODE_PORT=${NODE_PORT:-30080}
METRICS_URL="${METRICS_URL:-http://localhost:$NODE_PORT/metrics}"
METRICS_OUT="$EVIDENCE/metrics_http_duration.csv"
# Host-side freeze probe target. Deliberately OUTSIDE the evidence tree so a
# scratch file never lands in committed evidence, and on the Windows volume
# rather than inside WSL2 -- C: is where the VHDX lives and what a VSS /
# Volsnap freeze actually blocks.
PROBE="${TMPDIR:-/tmp}/.polyforge_host_probe"
prev_epoch=""

# Single instance, enforced. A second observer contaminated Stage B attempt 3:
# an earlier one was still alive because the kill that was meant to stop it used
# `pkill`, which does NOT EXIST in Git Bash -- and the error went to /dev/null,
# so the operator believed it had stopped. Two observers doubled the sampling
# load on the box running the cluster AND interleaved rows into one CSV, where
# the older instance's stale readiness column disagreed with the newer one's.
# Evidence that argues with itself is worse than no evidence.
# LIVENESS HEARTBEAT -- deliberately NOT the lock file, and deliberately not
# named like one.
#
# Attempt 9 was contaminated because a launcher ran `rm -f .observer.lock
# .controls.lock` before starting, which disarmed this guard completely: a
# second observer and a second fault injector ran against the live cluster for
# 13.7 h and applied four faults no pre-registration describes. The lock logic
# below was correct and never got to run. A guard a launcher can delete is not
# a guard, so the real check is a file the launcher does not know about,
# refreshed every loop, and read by whoever starts next.
HEARTBEAT="$REPO/.observer.heartbeat"
HEARTBEAT_STALE_S=${HEARTBEAT_STALE_S:-120}
beat() { date +%s > "$HEARTBEAT" 2>/dev/null || true; }
if [ -f "$HEARTBEAT" ]; then
  last=$(cat "$HEARTBEAT" 2>/dev/null || echo 0)
  age=$(( $(date +%s) - ${last:-0} ))
  if [ "$age" -lt "$HEARTBEAT_STALE_S" ]; then
    echo "another observer beat ${age}s ago; refusing to start a second" >&2
    exit 3
  fi
fi
beat

LOCK="$REPO/.observer.lock"
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "observer already running as PID $(cat "$LOCK"); refusing to start a second" >&2
  exit 3
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

# HOST EVENT TAIL (attempt 9 / PREREG_LIVE_SOAK_V7).
# V6 made the freeze measurable; it did not make it NAMEABLE. attempt 8's 54 s
# scheduling gap and 1412 ms host write prove the host stopped scheduling this
# loop, but a gap looks the same whether VSS froze the volume, the disk stack
# reset, or the box suspended. The tail runs for the life of the observer and
# writes named Windows System-log events into the same evidence directory, so
# a stall row in observer.csv can be joined to its cause by timestamp.
#
# Started HERE, by the observer, rather than left to the operator: every
# instrument this harness lost was one a human had to remember to launch.
HOST_EVT_PID=""
if command -v powershell.exe >/dev/null 2>&1; then
  # MSYS_NO_PATHCONV: Git Bash rewrites these arguments into Windows paths
  # halfway and hands PowerShell something that does not exist. This cost four
  # separate sessions before it was written down.
  MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -ExecutionPolicy Bypass \
    -File "$(cygpath -w "$REPO/scripts/host_event_tail.ps1")" \
    -OutFile "$(cygpath -w "$EVIDENCE/host_events.csv")" \
    -IntervalSeconds 60 >> "$EVIDENCE/host_events.log" 2>&1 &
  HOST_EVT_PID=$!
  echo "host event tail started as PID $HOST_EVT_PID" >&2
else
  echo "WARNING: powershell.exe not on PATH -- host events will NOT be recorded" >&2
fi
# kill -9, not kill: plain kill does not reliably stop these under Git Bash.
trap 'rm -f "$LOCK"; [ -n "$HOST_EVT_PID" ] && kill -9 "$HOST_EVT_PID" 2>/dev/null' EXIT INT TERM

# Cumulative counters, so a spike between two samples is still visible as a
# delta even though the sample itself missed the moment.
echo "epoch,iso,nodes_mem_pct,pods_ready,pods_total,restarts_total,max_pod_mem_mi,cp_replicas,pg_ckpt_timed,pg_ckpt_req,pg_ckpt_write_ms,pg_ckpt_sync_ms,pg_rows_ingested,pg_autovacuum,kind_node_mem,sample_gap_s,host_write_ms,http_dur_sum_s,http_dur_count" > "$OUT"
# Full histogram fidelity goes to its own file: the bucket rows are what a p95
# needs, and folding eleven buckets into observer.csv would bury the columns
# that already earn their place there.
echo "epoch,metric" > "$METRICS_OUT"

psql_scalar() { # SQL -> single value, empty on any failure
  kubectl --namespace "$NS" exec deploy/postgres -- \
    psql -U postgres -d polyforge -tAc "$1" 2>/dev/null | tr -d '\r' | head -1
}

while true; do
  epoch=$(date +%s)
  iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Node memory as a percentage of allocatable: the WSL2 VM's ceiling is the
  # one resource this box is actually short of.
  nodes_mem_pct=$(kubectl top nodes --no-headers 2>/dev/null |
    awk '{gsub(/%/,"",$5); if ($5+0 > m) m = $5+0} END {printf "%d", m+0}')

  pods=$(kubectl --namespace "$NS" get pods --no-headers 2>/dev/null)
  pods_total=$(printf '%s\n' "$pods" | grep -c . )
  # split, not a backreference: awk is POSIX ERE and has none, so the old
  # pattern matched nothing and this column read 0 on a run where all twenty
  # pods were healthy.
  pods_ready=$(printf '%s\n' "$pods" |
    awk '{split($2, a, "/"); if (a[1] == a[2] && a[1] + 0 > 0 && $3 == "Running") n++} END {printf "%d", n+0}')
  # Restart count is the single number that would have settled attempt 4's
  # "did the pods OOM or did the load path die" question on the spot.
  restarts_total=$(printf '%s\n' "$pods" | awk '{s += $4} END {printf "%d", s+0}')
  max_pod_mem_mi=$(kubectl --namespace "$NS" top pods --no-headers 2>/dev/null |
    awk '{gsub(/Mi/,"",$3); if ($3+0 > m) m = $3+0} END {printf "%d", m+0}')
  cp_replicas=$(kubectl --namespace "$NS" get deploy polyforge-control-plane \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null)

  # PostgreSQL: checkpoints and their write/sync time are the standard
  # explanation for a multi-second write stall on slow storage, and pgdata is
  # an emptyDir on a VHDX here. In PG16 these live in pg_stat_bgwriter; in
  # PG17+ they moved to pg_stat_checkpointer, so try both.
  ckpt=$(psql_scalar "SELECT num_timed||','||num_requested||','||write_time::bigint||','||sync_time::bigint FROM pg_stat_checkpointer")
  if [ -z "$ckpt" ]; then
    ckpt=$(psql_scalar "SELECT checkpoints_timed||','||checkpoints_req||','||checkpoint_write_time::bigint||','||checkpoint_sync_time::bigint FROM pg_stat_bgwriter")
  fi
  pg_ckpt_timed=$(echo "$ckpt" | cut -d, -f1)
  pg_ckpt_req=$(echo "$ckpt" | cut -d, -f2)
  pg_ckpt_write_ms=$(echo "$ckpt" | cut -d, -f3)
  pg_ckpt_sync_ms=$(echo "$ckpt" | cut -d, -f4)
  # n_tup_ins, not count(*): the row count is the point, but a sequential scan
  # every 20 s would make the observer part of the load it is measuring.
  pg_rows_ingested=$(psql_scalar "SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname = 'telemetry_events'")
  pg_autovacuum=$(psql_scalar "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'autovacuum worker'")

  # Container-level memory for the kind nodes, which is what the VM ceiling
  # actually bites on.
  kind_node_mem=$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null |
    awk '/control-plane|worker/ {print $1"="$2}' | paste -sd'|' -)

  # HOST FREEZE PROBE (attempt 8 / PREREG_LIVE_SOAK_V6).
  # Attempt 7's multi-second stall was INFERRED -- from telemetry ingestion
  # collapsing, checkpoints spanning the stall windows, and a Windows Volsnap
  # event 36 found in the host log afterwards. Nothing MEASURED it, and V5 said
  # so in writing: "the stall's cause is still not proven". These two columns
  # measure it directly. Both are host-side, so they still record a freeze in
  # the case where kubectl is itself the thing that is blocked:
  #
  #   sample_gap_s  - wall-clock seconds since the previous sample. The loop
  #                   sleeps $INTERVAL, so a gap materially above the interval
  #                   means this process was not scheduled at all. Attempt 7's
  #                   51.4 s stall would appear here as a ~51 s excess.
  #   host_write_ms - latency of one small write to the Windows volume, which a
  #                   VSS/Volsnap freeze blocks and CPU-bound work does not.
  #
  # Neither touches the cluster. This is added measurement, not a change to
  # what is being measured.
  t0=$(date +%s%N 2>/dev/null || echo 0)
  echo "$epoch" > "$PROBE" 2>/dev/null
  t1=$(date +%s%N 2>/dev/null || echo 0)
  if [ "$t0" != "0" ] && [ "$t1" != "0" ]; then
    host_write_ms=$(( (t1 - t0) / 1000000 ))
  else
    host_write_ms=""
  fi
  if [ -n "$prev_epoch" ]; then sample_gap_s=$(( epoch - prev_epoch )); else sample_gap_s=""; fi
  prev_epoch=$epoch

  # True service latency. Fails to empty like every other field here: a
  # metrics scrape must never be the reason a 24 h sitting stops.
  http_dur_sum_s=""
  http_dur_count=""
  metrics=$(curl -fsS --max-time 5 "$METRICS_URL" 2>/dev/null |
    grep '^polyforge_http_request_duration_seconds' | grep 'replay' || true)
  if [ -n "$metrics" ]; then
    printf '%s\n' "$metrics" | awk -v e="$epoch" '{print e "," $0}' >> "$METRICS_OUT"
    # Cumulative counters, so consecutive samples give an interval mean even
    # though the histogram buckets are what a percentile needs.
    http_dur_sum_s=$(printf '%s\n' "$metrics" |
      awk '/_sum/ {s += $NF} END {if (s > 0) printf "%.6f", s}')
    http_dur_count=$(printf '%s\n' "$metrics" |
      awk '/_count/ {c += $NF} END {if (c > 0) printf "%d", c}')
  fi

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$epoch" "$iso" "$nodes_mem_pct" "$pods_ready" "$pods_total" \
    "$restarts_total" "$max_pod_mem_mi" "$cp_replicas" \
    "$pg_ckpt_timed" "$pg_ckpt_req" "$pg_ckpt_write_ms" "$pg_ckpt_sync_ms" \
    "$pg_rows_ingested" "$pg_autovacuum" "$kind_node_mem" "$sample_gap_s" "$host_write_ms" \
    "$http_dur_sum_s" "$http_dur_count" >> "$OUT"

  beat
  sleep "$INTERVAL"
done
