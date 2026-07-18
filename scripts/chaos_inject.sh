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

echo "[inject] waiting for the k6 load window to start..."
for _ in $(seq 1 480); do
  pgrep -x k6 >/dev/null 2>&1 && break
  sleep 5
done
if ! pgrep -x k6 >/dev/null 2>&1; then
  echo "[inject] ERROR: k6 never started within 40min; aborting injector"
  exit 1
fi
T0=$(date +%s)
echo "[inject] k6 load started at $(ts) (T0=$T0)"

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
pods=$(kubectl -n "$NS" get pods -l app.kubernetes.io/name=polyforge-operator-planner -o name 2>/dev/null)
[ -z "$pods" ] && pods=$(kubectl -n "$NS" get pods -o name 2>/dev/null | grep -i planner || true)
if [ -n "$pods" ]; then
  echo "[inject] deleting planner pod(s): $pods"
  echo "$pods" | xargs -r kubectl -n "$NS" delete --wait=false 2>&1 | sed 's/^/[inject] /'
else
  echo "[inject] WARN: no planner pod matched; deployments present:"
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
