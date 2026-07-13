#!/usr/bin/env bash
# Phase 7 runbook: the live ordinal confirmation, one command.
#
#   bash scripts/phase7_kind_run.sh               # hpa smoke (verified 16d)
#   bash scripts/phase7_kind_run.sh --jcac-smoke  # first live jcac run
#   bash scripts/phase7_kind_run.sh --full        # the ordinal slice YAML
#
# Requires a Docker-capable machine (GitHub Codespace built from
# .devcontainer/, an Oracle Free VM, or any Linux box with the tools).
# This script builds all three images locally and lets the harness
# side-load them into each per-run kind cluster (`kind load` steps in
# eval/harness/cluster_backend.command_plan) — nothing is pulled from a
# registry.
#
# The jcac arm's actuation gate is executable: command_plan ends its
# operator install with `kubectl wait --for=condition=Applied` on every
# Policy, so a run in which the operator never scaled the target
# Deployment fails before k6 sends a single request.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== preflight =="
(cd eval && python -c "
from harness import cluster_backend
missing = cluster_backend.preflight()
if missing:
    raise SystemExit(f'missing tools: {missing}')
print('all cluster-backend tools present')
")

echo "== build images (control plane, operator, planner) =="
docker build -t polyforge/control-plane:dev .
docker build -f Dockerfile.operator -t polyforge/operator:dev .
docker build -f services/planner/Dockerfile -t polyforge/planner:dev .

echo "== run =="
case "${1:-}" in
--full)
    # 12 runs ~ 5 h wall. Run the two smokes first on any fresh machine.
    (cd eval && python -m harness.runner experiments/phase7_live.yaml --workers 1)
    echo "== done — results in eval/results/phase7_live.duckdb =="
    ;;
--jcac-smoke)
    # First live jcac execution: 1 run, 5 min of load, expect a bug tail
    # like the hpa smoke's five — fix and commit each finding.
    (cd eval && python -m harness.runner experiments/phase7_jcac_smoke.yaml --workers 1)
    echo "== done — results in eval/results/phase7_jcac_smoke.duckdb =="
    ;;
*)
    (cd eval && python -m harness.runner experiments/phase7_smoke.yaml --workers 1)
    echo "== done — results in eval/results/phase7_smoke.duckdb =="
    ;;
esac
