#!/usr/bin/env bash
# Is the apiserver-throttle fault actually throttling anything?
#
# WHY THIS EXISTS. Across WP14 the throttle has never been shown to perturb
# the system. Attempt 4 logged 20-27 reconciler errors during throttle
# windows, which looked like an effect until the Policy conflict storm was
# fixed: the background rate was ~9 errors per 2 minutes, so a 90 s window
# captured a handful whether or not the throttle did anything. With conflicts
# eliminated, Stage B measured delta 0 twice.
#
# So either the FlowSchema is not matching the operator's requests, or the
# operator's request volume never reaches the concurrency limit. Those need
# different fixes, and guessing between them is how a fault stays cosmetic for
# four more attempts -- which is exactly what happened to the planner fault.
#
# Reads the apiserver's own APF accounting, which is authoritative: it counts
# what was dispatched and rejected per priority level, rather than inferring
# from downstream symptoms.
#
#   scripts/diagnose_throttle.sh [duration_s]

set -uo pipefail
NS=polyforge
DURATION=${1:-90}

# Git Bash (MSYS) rewrites a leading-slash argument into a Windows path, so
# `kubectl get --raw /metrics` asked the apiserver for
# "C:/Program Files/Git/metrics" and got a 404. With 2>/dev/null on the read
# that surfaced as an empty string, which the arithmetic below turned into 0 --
# and the script then printed "the FlowSchema matched NOTHING" with total
# confidence, from a measurement it had never taken. This is the same defect
# class as the vacuous hypotheses: an instrument that reports an answer when it
# read nothing at all.
export MSYS_NO_PATHCONV=1

apf_metric() { # metric-substring -> summed value for our priority level
  kubectl get --raw /metrics 2>/dev/null |
    grep -E "^$1\{.*(priority_level|priorityLevel)=\"pf-chaos-throttle\"" |
    awk '{s+=$NF} END {printf "%.0f", s+0}'
}

SA=$(kubectl -n "$NS" get deploy -l app.kubernetes.io/name=polyforge-operator \
      -o jsonpath='{.items[0].spec.template.spec.serviceAccountName}' 2>/dev/null)
echo "operator ServiceAccount: ${SA:-<not found>}"

cat <<YAML | kubectl apply -f -
apiVersion: flowcontrol.apiserver.k8s.io/v1
kind: PriorityLevelConfiguration
metadata:
  name: pf-chaos-throttle
spec:
  type: Limited
  limited:
    nominalConcurrencyShares: 1
    # Without this the level borrows spare concurrency from other levels, so
    # a "limit" of 1 share is not a limit at all when the cluster is quiet --
    # a plausible reason the fault has never bitten.
    borrowingLimitPercent: 0
    lendablePercent: 0
    limitResponse:
      type: Reject
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
            name: ${SA:-polyforge-operator}
            namespace: $NS
      resourceRules:
        - verbs: ["*"]
          apiGroups: ["*"]
          resources: ["*"]
          clusterScope: true
          namespaces: ["*"]
YAML

# A verdict computed from an unreadable endpoint is worse than no verdict.
if [ "$(kubectl get --raw /metrics 2>/dev/null | grep -c flowcontrol)" -eq 0 ]; then
  echo "ABORT: the apiserver returned no flowcontrol metrics, so nothing below" >&2
  echo "       would be a measurement. Check the /metrics path is reachable" >&2
  echo "       (MSYS_NO_PATHCONV on Git Bash) before trusting any verdict." >&2
  kubectl delete flowschema pf-chaos-throttle >/dev/null 2>&1
  kubectl delete prioritylevelconfiguration pf-chaos-throttle >/dev/null 2>&1
  exit 2
fi

echo "throttle applied; sampling APF counters for ${DURATION}s"
sleep 5
d0=$(apf_metric apiserver_flowcontrol_dispatched_requests_total)
r0=$(apf_metric apiserver_flowcontrol_rejected_requests_total)
sleep "$DURATION"
d1=$(apf_metric apiserver_flowcontrol_dispatched_requests_total)
r1=$(apf_metric apiserver_flowcontrol_rejected_requests_total)
seats=$(apf_metric apiserver_flowcontrol_current_limit_seats)

# Matching probe. Without this, an idle operator and a non-matching FlowSchema
# are the same observation -- zero dispatched -- and the script previously
# reported the second while observing the first. Impersonating the operator's
# ServiceAccount produces traffic with the identity the rule selects on, so a
# zero here means the RULE is wrong rather than the workload being quiet.
for _ in $(seq 1 40); do
  kubectl get configmaps -n "$NS"     --as="system:serviceaccount:${NS}:${SA:-polyforge-operator}" >/dev/null 2>&1
done
sleep 2
d2=$(apf_metric apiserver_flowcontrol_dispatched_requests_total)

kubectl delete flowschema pf-chaos-throttle >/dev/null 2>&1
kubectl delete prioritylevelconfiguration pf-chaos-throttle >/dev/null 2>&1

observed=$((d1 - d0))
probed=$((d2 - d1))
rejected=$((r1 - r0))
echo
echo "  dispatched from the operator itself    : ${observed}"
echo "  dispatched from the identity probe     : ${probed}  (40 impersonated GETs)"
echo "  rejected  by the throttled level       : ${rejected}"
echo "  concurrency seats at the limit         : ${seats:-unknown}"
echo
if [ "$probed" -eq 0 ]; then
  echo "VERDICT: the FlowSchema matched NOTHING -- not even requests carrying the"
  echo "         operator's own identity. The rule is wrong, so the fault is inert"
  echo "         and any hypothesis scored on it is untested. Fix the matching"
  echo "         rule; do not lower the threshold."
elif [ "$observed" -eq 0 ]; then
  echo "VERDICT: the rule MATCHES (the probe went through it) but the operator"
  echo "         sent nothing during the window. Inconclusive about behaviour"
  echo "         under load: re-run this while the load window is open."
elif [ "$rejected" -eq 0 ]; then
  echo "VERDICT: the operator's requests ARE being classified into the throttled"
  echo "         level, and none were rejected. Its concurrency never reaches the"
  echo "         ${seats:-?}-seat limit -- and nominalConcurrencyShares is already 1,"
  echo "         the smallest APF accepts, so this fault cannot be tightened into"
  echo "         biting on this apiserver. It needs a busier operator or a"
  echo "         different mechanism, NOT a different selector."
else
  echo "VERDICT: the throttle is REJECTING operator requests. The fault has a"
  echo "         real mechanism; whether the controller degrades measurably is"
  echo "         then a genuine question rather than a vacuous one."
fi
