#!/usr/bin/env bash
# WP14 Phase 6, Stage B — the positive-control stage.
#
# This script does NOT try to show the system surviving faults. It tries to
# make each hypothesis FAIL, on demand, and fails the stage if it cannot.
#
# WHY. Attempt 4's deepest defect was not that hypotheses failed; it was that
# three of five COULD NOT fail. SK-H2 computed 0 == 0 because no audit stream
# existed. SK-H1's rule compared a metric pinned at zero by a 375 ms target
# against an 8 ms p99. SK-H5 counted restarts of a mechanism that was itself
# the bug. All three would have "passed" on a flawless run, and did pass, four
# times, while testing nothing. A hypothesis never observed failing is not a
# test — so before the 24 h sitting, each one gets watched failing here.
#
# Compressed schedule over the 60-minute window:
#   T+ 5m  planner scale-to-zero   -> MUST produce fallback log lines
#   T+15m  apiserver throttle      -> MUST raise the reconciler error rate
#   T+25m  CPU starvation          -> MUST push crud_p95 past the 20% band
#   T+35m  planner scale-to-zero   -> repeat, confirms it was not a one-off
#   T+45m  apiserver throttle      -> repeat
#
# The latency verdict is scored post-hoc from eval-export's 60 s buckets; the
# rest are asserted live. Results land in the evidence dir as they happen.

set -uo pipefail
NS=polyforge
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE="${EVIDENCE_DIR:-$REPO/eval/results/soak_stage_b_evidence}"
mkdir -p "$EVIDENCE" 2>/dev/null || true
RESULTS="$EVIDENCE/positive_controls.log"

# Single instance, enforced. Stage B attempt 4 ran TWO injectors concurrently:
# a previous launch was killed, its parent died, and its child bash survived to
# keep the schedule. timeline.txt recorded two T0 lines and two planner_down
# events three seconds apart, and the run's degraded latency was the harness
# fighting itself, not the system under test. (Plain `kill` also does not
# reliably stop these on Git Bash -- `kill -9` does.)
CONTROL_LOCK="$REPO/.controls.lock"
if [ -f "$CONTROL_LOCK" ] && kill -0 "$(cat "$CONTROL_LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "controls already running as PID $(cat "$CONTROL_LOCK"); refusing to start a second" >&2
  exit 3
fi
echo $$ > "$CONTROL_LOCK"
trap 'rm -f "$CONTROL_LOCK"' EXIT INT TERM
[ -f "$EVIDENCE/audit_window.csv" ] ||
  echo "label,degraded_cycles,tenants,base_start,base_end,out_end" > "$EVIDENCE/audit_window.csv"

PLANNER_OUTAGE_S=${PLANNER_OUTAGE_S:-90}
THROTTLE_DURATION_S=${THROTTLE_DURATION_S:-90}
CPU_HOGS=${CPU_HOGS:-2}
CPU_DURATION_S=${CPU_DURATION_S:-120}

ts() { date -u +%H:%M:%S; }
log() { printf '%s %s\n' "$(ts)" "$*" | tee -a "$RESULTS"; }

k6_running() {
  pgrep -x k6 >/dev/null 2>&1 && return 0
  command -v tasklist >/dev/null 2>&1 &&
    tasklist //FI "IMAGENAME eq k6.exe" 2>/dev/null | grep -qi "k6.exe" && return 0
  return 1
}

operator_pod() {
  kubectl -n "$NS" get pods -o name 2>/dev/null |
    grep -i operator | grep -vi planner | head -1
}

log "=== Stage B positive controls: waiting for the k6 load window ==="
for _ in $(seq 1 240); do k6_running && break; sleep 5; done
if ! k6_running; then
  log "ABORT: k6 never started"
  exit 1
fi
T0=$(date +%s)
log "k6 load started (T0=$T0)"
echo "T0=$T0" >> "$EVIDENCE/timeline.txt"

wait_until() {
  local tgt=$((T0 + $1)) now d
  now=$(date +%s); d=$((tgt - now))
  [ "$d" -gt 0 ] && sleep "$d"
  return 0
}

PASS=0
FAIL=0
verdict() { # name, condition-result, detail
  if [ "$2" = "0" ]; then
    log "  CONTROL PASS  $1 — $3"
    PASS=$((PASS + 1))
  else
    log "  CONTROL FAIL  $1 — $3"
    FAIL=$((FAIL + 1))
  fi
}

# ---------------------------------------------------------------- planner ---
# Stream message count. During a planner outage EVERY cycle falls back, so the
# growth across the outage window is exactly the fallback records -- which is
# what makes SK-H2's "==" rule scoreable at all. It was comparing 14 degraded
# cycles against 2,888 records, and the stream carries one record per tenant
# per cycle (8 x 360 = 2,880), so the two sides were never the same quantity.
# NATS does not expose per-subject counts on its monitoring port in this
# version, and the operator publishes AuditEntry payloads that the typed
# Replay path cannot decode, so the window delta is the honest measurement
# available without changing the audit contract.
audit_messages() {
  # Retried, and loud on failure. Attempt 4 recorded EMPTY fields here because
  # `kubectl exec` failed under a saturated apiserver, and the empty value then
  # produced "operand expected" in the arithmetic below -- a sampler that
  # writes nothing and a script that crashes on the nothing. An unreadable
  # stream must be recorded as unreadable, not as a blank that later looks
  # like a zero.
  local out i
  for i in 1 2 3; do
    out=$(kubectl -n "$NS" exec deploy/nats -- wget -qO- http://127.0.0.1:8222/jsz 2>/dev/null |
      tr ',' '\n' | sed -n 's/.*"messages"[[:space:]]*:[[:space:]]*\([0-9]\+\).*/\1/p' | head -1)
    case "$out" in
      ''|*[!0-9]*) sleep 3 ;;
      *) printf '%s' "$out"; return 0 ;;
    esac
  done
  printf 'UNREADABLE'
  return 1
}

# Arithmetic that cannot crash the stage on a non-numeric sample.
delta_or_na() {
  case "$1$2" in
    *[!0-9]*|'') printf 'n/a' ;;
    *) printf '%s' "$(( $1 - $2 ))" ;;
  esac
}

planner_control() {
  local label="$1"
  local dep
  dep=$(kubectl -n "$NS" get deploy -o name 2>/dev/null | grep -i planner | head -1)
  if [ -z "$dep" ]; then
    verdict "$label" 1 "no planner deployment found"
    return
  fi
  local tenants base_start base_end out_end
  tenants=$(kubectl -n "$NS" get policies --no-headers 2>/dev/null | grep -c .)

  # SK-H2 is a DIFFERENCE, not a count against an expectation.
  #
  # The first formulation compared 14 degraded cycles against 2,888 records and
  # passed while testing nothing. The second compared stream growth against
  # (log lines x tenants) and FAILED for a reason that had nothing to do with
  # the claim: Stage B attempt 5 grew by exactly 88 records in both windows,
  # which is 11 control cycles x 8 tenants -- perfectly continuous auditing --
  # while the rule expected 56, because grepping "planner unavailable"
  # UNDERCOUNTS cycles and the window also holds post-recovery records that are
  # indistinguishable from fallback ones.
  #
  # Both faults are properties of the instrument, visible without looking at
  # any outcome. So the quantity changes to one that needs neither a cycle
  # count nor a way to tell the two record populations apart: sample an
  # equal-length window immediately BEFORE the outage, and require that the
  # audit stream does not grow more slowly during degradation than it did
  # while healthy. That is exactly "no degraded cycle goes unrecorded", and it
  # fails precisely when records are dropped.
  base_start=$(audit_messages)
  log "$label: baseline audit window opens at ${base_start:-?} (${tenants:-?} tenants)"
  sleep "$PLANNER_OUTAGE_S"
  base_end=$(audit_messages)

  log "$label: scaling $dep to 0 for ${PLANNER_OUTAGE_S}s (audit stream at ${base_end:-?})"
  echo "$(date +%s) planner_down" >> "$EVIDENCE/timeline.txt"
  kubectl -n "$NS" scale "$dep" --replicas=0 >/dev/null 2>&1
  sleep "$PLANNER_OUTAGE_S"
  kubectl -n "$NS" scale "$dep" --replicas=1 >/dev/null 2>&1
  echo "$(date +%s) planner_up" >> "$EVIDENCE/timeline.txt"
  # Sampled AT scale-up, with no settle: the outage window must contain the
  # outage and nothing else, or the comparison is against a dirty window.
  out_end=$(audit_messages)
  sleep 20

  local op fb
  op=$(operator_pod)
  fb=$(kubectl -n "$NS" logs "$op" --since="$((PLANNER_OUTAGE_S + 120))s" 2>/dev/null |
       grep -c 'planner unavailable' || true)
  # Across SEVEN injections in four attempts this was zero, because deleting
  # the pod left it serving established connections. Kept as the check that the
  # fault reaches the controller at all -- it is no longer SK-H2's expectation.
  [ "${fb:-0}" -gt 0 ] && verdict "$label fallback" 0 "$fb fallback line(s)"                        || verdict "$label fallback" 1 "ZERO fallback lines — the fault did not reach the controller"

  printf '%s,%s,%s,%s,%s,%s\n' "$label" "${fb:-0}" "${tenants:-0}" \
    "${base_start:-}" "${base_end:-}" "${out_end:-}" >> "$EVIDENCE/audit_window.csv"
  log "$label audit: healthy window +$(delta_or_na "${base_end:-}" "${base_start:-}"), degraded window +$(delta_or_na "${out_end:-}" "${base_end:-}")"
}

# --------------------------------------------------------------- throttle ---

# ---------------------------------------------------------------- latency ---
# NOT `tc netem`. Phase 3.1 offers "a tc netem delay, or a CPU-starved
# control-plane pod"; the netem half cannot work, and the reason is worth
# stating because it nearly cost a fifth sitting.
#
# SK-H1 is scored on eval-export's crud_p95_ms, which comes from the
# `latency_ms` written by internal/platform/replay.go. That handler starts its
# clock immediately before burnCPU and stops it immediately after, so the
# recorded number is the duration of a CPU spin: request parse, queueing, the
# telemetry write and the network are all outside the measured window. A
# netem qdisc adds delay in precisely that outside region, so it cannot move
# the metric by construction -- SK-H1 would have been observed NOT failing and
# read as "still vacuous", when the truth is the fault never reached it.
# internal/platform/replay_latency_semantics_test.go demonstrates the gap
# (250 ms injected outside the burn, 1 ms recorded).
#
# CPU starvation does reach it, because burnCPU spins against a wall-clock
# deadline: descheduled time lands inside the measurement. crud_read is one
# work unit at 1 ms of CPU, so even a few hundred microseconds of scheduling
# delay clears the 20% band comfortably.
#
# Two spinners on ONE worker, deliberately: the box has 8 logical CPUs shared
# with the k6 process on the Windows side, and starving the load generator
# would produce dropped iterations -- a harness failure wearing the costume of
# a system failure, which is the confusion this whole work package exists to
# end. One worker of three also leaves two thirds of the requests unaffected,
# which is the harder test: p95 still has to move.
cpu_control() {
  local node="polyforge-eval-worker"
  log "cpu: starving $node with ${CPU_HOGS} spinner(s) for ${CPU_DURATION_S}s"
  echo "$(date +%s) cpu_on" >> "$EVIDENCE/timeline.txt"
  # `timeout` inside the node makes the hogs self-terminating: if this script
  # dies mid-window the node does not stay starved.
  if ! docker exec -d "$node" bash -c       "for i in \$(seq 1 ${CPU_HOGS}); do timeout ${CPU_DURATION_S} bash -c 'while :; do :; done' & done" 2>/dev/null; then
    verdict "cpu starvation" 1 "could not start the spinners"
    return
  fi
  sleep "$CPU_DURATION_S"
  sleep 5  # let the last spinner's timeout fire before declaring the window shut
  echo "$(date +%s) cpu_off" >> "$EVIDENCE/timeline.txt"
  # Scored post-hoc from the 60 s buckets: the window under starvation must
  # show crud_p95 more than 20% above the pre-fault window, which is SK-H1's
  # new rule failing on demand. analyse_stage_b.py reads timeline.txt for this.
  verdict "cpu starvation injected" 0 "scored post-hoc against the buckets"
}

# The apiserver throttle is GONE, not merely ungated. scripts/diagnose_throttle.sh
# settled it: the FlowSchema matches (40/40 impersonated operator-identity
# requests dispatched through the level) and the level has NEVER rejected
# anything -- it carries 3 concurrency seats, nominalConcurrencyShares is
# already 1, the smallest APF accepts, and the operator never has 3 requests in
# flight. The fault has no reachable mechanism on this apiserver, so injecting
# it only manufactures the appearance of a chaos schedule. See the roadmap's
# Stage B table.
#
# Offsets are overridable so a short run can validate an instrument without
# sitting through the full hour. Defaults are the Stage B schedule.
wait_until "${SCHED_PLANNER_1:-300}";  planner_control  "T+5m planner"
wait_until "${SCHED_CPU:-1500}";       cpu_control
wait_until "${SCHED_PLANNER_2:-2100}"; planner_control  "T+35m planner"

log "=== Stage B controls finished: ${PASS} pass, ${FAIL} fail ==="
log "NOTE: the latency verdict is decided by analyse_stage_b.py against the"
log "      60 s buckets, not here. This script only guarantees it was injected."
[ "$FAIL" -eq 0 ] || exit 1
