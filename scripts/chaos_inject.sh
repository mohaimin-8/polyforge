#!/usr/bin/env bash
# Fault injector for PREREG_LIVE_CHAOS_P99.md. Runs alongside a live jcac run
# (eval/experiments/phase7_chaos_p99.yaml) and injects the two pre-declared
# faults at the frozen offsets into the k6 load window.
#
#   Fault 1 (primary): planner crash at T=8min. Deletes the operator's planner
#     pod; the operator must hold its last-known-good plan and the Deployment
#     reschedules. This is the live analogue of the sim's chaos_planner_outage
#     (RESULTS_CHAOS_SIM.md CH-H1/CH-H2).
#   Fault 2 (best-effort): apiserver throttle at T=14min. Applies a restrictive
#     APF PriorityLevelConfiguration + FlowSchema against the operator's
#     ServiceAccount for 60s so its reconcile requests are starved, then
#     removes them. Logged and skipped if APF cannot apply on this cluster.
#
# T=0 is the start of the k6 load window (detected by pgrep). Offsets and the
# throttle duration are overridable via env for a shorter dry run.
set -uo pipefail
NS=polyforge
PLANNER_OFFSET_S=${PLANNER_OFFSET_S:-480}     # T=8min
THROTTLE_OFFSET_S=${THROTTLE_OFFSET_S:-840}   # T=14min
THROTTLE_DURATION_S=${THROTTLE_DURATION_S:-60}
ts() { date -u +%H:%M:%S; }

# Injector output goes straight into the repo evidence directory rather than a
# temp path the operator has to remember to copy. WP14 attempt 4 was snapshotted
# by hand at hour 17 and captured only 5 of its 8 faults; the record was
# regenerated later only because the miss was noticed. Writing here removes the
# step that can be forgotten.
EVIDENCE_DIR=${EVIDENCE_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/eval/results/live_soak_evidence"}
mkdir -p "$EVIDENCE_DIR" 2>/dev/null || true
exec > >(tee -a "$EVIDENCE_DIR/inject_${PLANNER_OFFSET_S}.log") 2>&1

# T0 is the opening of the SCORED load window. The harness marks that instant
# by writing .soak-running with `started=<epoch>` (cluster_backend.execute) and
# removes the file when the run ends. Detecting k6 -- the old rule, first by
# pgrep and then, after WP14's first soak lost all eight faults to a native
# k6.exe that pgrep cannot see under Git Bash, by tasklist -- keyed on the
# FIRST k6 process, which since the warm-up was added is the discarded
# warm-up: every fault would have fired at least WARMUP_SECONDS (90 s) early
# (audit 2026-09-26). The marker names the scored window on every host.
#
# A marker older than this injector (beyond a tolerance for launching just
# after the window opened) is a leftover from an earlier, crashed run and is
# not taken for this one.
MARKER=${SOAK_MARKER:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.soak-running"}
MARKER_TOLERANCE_S=${MARKER_TOLERANCE_S:-300}
LAUNCHED=$(date +%s)
marker_started() {
  [ -f "$MARKER" ] && sed -n 's/^started=\([0-9][0-9]*\).*/\1/p' "$MARKER" | head -1
}

echo "[inject] waiting for the scored load window to open ($MARKER)..."
T0=""
for _ in $(seq 1 480); do
  started=$(marker_started)
  if [ -n "$started" ] && [ "$started" -ge $((LAUNCHED - MARKER_TOLERANCE_S)) ]; then
    T0=$started
    break
  fi
  sleep 5
done
if [ -z "$T0" ]; then
  echo "[inject] ERROR: the scored load window never opened within 40min; aborting injector"
  exit 1
fi
echo "[inject] scored load window opened at $(date -u -d "@$T0" +%H:%M:%S 2>/dev/null || echo "$T0") (T0=$T0)"

wait_until() {
  local tgt=$((T0 + $1)) now
  now=$(date +%s)
  local d=$((tgt - now))
  [ "$d" -gt 0 ] && sleep "$d"
}

# --- Fault 1: planner crash (T=8min) ---
wait_until "$PLANNER_OFFSET_S"
echo "[inject] === FAULT 1 planner-crash at $(ts) (T+${PLANNER_OFFSET_S}s) ==="
kubectl -n "$NS" get pods -o wide 2>&1 | sed 's/^/[inject] /' || true

# WP14 attempt 5: SCALE TO ZERO, do not delete the pod.
#
# Deleting the pod does not stop the planner answering. Across SEVEN
# planner-crash injections in four attempts, `fallback()` produced zero
# `planner unavailable, holding last good plan` lines. Attempt 4 measured why:
# fault 7's endpoint gap was 26 s -- 2.6x the 10 s plan interval, so the plan
# loop certainly ran inside it -- yet the pod reported **1/1 ready throughout
# termination**. Kubernetes delists a deleting pod from Endpoints at once but
# does not close established connections, and the operator reaches the planner
# over a reused HTTP connection. It kept talking to the dying pod and never
# saw an outage. The "planner crash" fault was cosmetic for four attempts.
#
# Scaling the Deployment to zero is unambiguous: no endpoints AND no process.
PLANNER_OUTAGE_S=${PLANNER_OUTAGE_S:-60}
PLANNER_DEPLOY=$(kubectl -n "$NS" get deploy -o name 2>/dev/null | grep -i planner | head -1)
if [ -n "$PLANNER_DEPLOY" ]; then
  echo "[inject] scaling $PLANNER_DEPLOY to 0 for ${PLANNER_OUTAGE_S}s"
  kubectl -n "$NS" scale "$PLANNER_DEPLOY" --replicas=0 2>&1 | sed 's/^/[inject] /'
  sleep "$PLANNER_OUTAGE_S"
  kubectl -n "$NS" scale "$PLANNER_DEPLOY" --replicas=1 2>&1 | sed 's/^/[inject] /'
  echo "[inject] planner restored at $(ts)"

  # POSITIVE CONTROL. A fault that does not perturb the system is not a
  # fault, and nothing checked this for four attempts. If the operator never
  # reports holding its last good plan, the injection did nothing and the
  # hypothesis it feeds is untested -- so say so loudly rather than let a
  # silent no-op be scored as a survived fault.
  OP_POD=$(kubectl -n "$NS" get pods -o name 2>/dev/null |
           grep -i 'operator' | grep -vi 'planner' | head -1)
  if [ -n "$OP_POD" ]; then
    FB=$(kubectl -n "$NS" logs "$OP_POD" --since="$((PLANNER_OUTAGE_S + 120))s" 2>/dev/null |
         grep -c 'planner unavailable' || true)
    echo "[inject] positive control: $FB fallback line(s) during the outage"
    if [ "${FB:-0}" -eq 0 ]; then
      echo "[inject] ERROR: planner outage produced NO fallback -- the fault"
      echo "[inject] ERROR: did not reach the controller. This is the defect"
      echo "[inject] ERROR: that made 7 injections across 4 attempts vacuous."
    fi
  fi
else
  echo "[inject] WARN: no planner deployment matched; deployments present:"
  kubectl -n "$NS" get deploy 2>&1 | sed 's/^/[inject] /' || true
fi

# --- Fault 2: apiserver throttle (T=14min), best-effort APF ---
wait_until "$THROTTLE_OFFSET_S"
echo "[inject] === FAULT 2 apiserver-throttle at $(ts) (T+${THROTTLE_OFFSET_S}s) ==="
SA=$(kubectl -n "$NS" get deploy -l app.kubernetes.io/name=polyforge-operator \
      -o jsonpath='{.items[0].spec.template.spec.serviceAccountName}' 2>/dev/null)
[ -z "$SA" ] && SA=polyforge-operator
echo "[inject] throttling ServiceAccount ${NS}/${SA} for ${THROTTLE_DURATION_S}s"
if cat <<YAML | kubectl apply -f - 2>&1 | sed 's/^/[inject] /'
apiVersion: flowcontrol.apiserver.k8s.io/v1
kind: PriorityLevelConfiguration
metadata:
  name: pf-chaos-throttle
spec:
  type: Limited
  limited:
    nominalConcurrencyShares: 1
    limitResponse:
      type: Queue
      queuing:
        queues: 1
        queueLengthLimit: 1
        handSize: 1
---
apiVersion: flowcontrol.apiserver.k8s.io/v1
kind: FlowSchema
metadata:
  name: pf-chaos-throttle
spec:
  matchingPrecedence: 1000
  priorityLevelConfiguration:
    name: pf-chaos-throttle
  rules:
    - subjects:
        - kind: ServiceAccount
          serviceAccount:
            name: ${SA}
            namespace: ${NS}
      resourceRules:
        - verbs: ["*"]
          apiGroups: ["*"]
          resources: ["*"]
          clusterScope: true
          namespaces: ["*"]
YAML
then
  echo "[inject] APF throttle applied; holding ${THROTTLE_DURATION_S}s"
  sleep "$THROTTLE_DURATION_S"
else
  echo "[inject] WARN: APF throttle could not apply; fault-2 skipped (best-effort)"
fi
kubectl delete flowschema pf-chaos-throttle --ignore-not-found 2>&1 | sed 's/^/[inject] /'
kubectl delete prioritylevelconfiguration pf-chaos-throttle --ignore-not-found 2>&1 | sed 's/^/[inject] /'
echo "[inject] throttle cleared at $(ts)"
echo "[inject] DONE at $(ts)"
