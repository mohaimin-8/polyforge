#!/usr/bin/env bash
# Phase 7 runbook: the live ordinal confirmation, one command.
#
#   bash scripts/phase7_kind_run.sh              # smoke: 1 run, jcac only
#   bash scripts/phase7_kind_run.sh --full       # the ordinal slice YAML
#
# Requires a Docker-capable machine (GitHub Codespace built from
# .devcontainer/, an Oracle Free VM, or any Linux box with the tools).
# This script builds the control-plane image locally and lets the harness
# side-load it into each per-run kind cluster (`kind load` step in
# eval/harness/cluster_backend.command_plan) — nothing is pulled from a
# registry.
#
# Known integration state (eval/README.md "Backends"): eval-export (point 1)
# is implemented; chart autoscaling toggles (point 2) and the
# /v1/workloads/replay data-plane endpoint (point 3) land during this first
# live session — expect the k6 step to surface point 3 first.
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

echo "== build control-plane image =="
docker build -t polyforge/control-plane:dev .

echo "== run =="
if [[ "${1:-}" == "--full" ]]; then
    # The full ordinal slice includes the jcac arm — do NOT run it until the
    # planner/operator is wired into the chart (eval/README.md point 2), or
    # a bare fixed-replica pod gets recorded under PolyForge's name.
    (cd eval && python -m harness.runner experiments/phase7_live.yaml --workers 1)
    echo "== done — results in eval/results/phase7_live.duckdb =="
else
    # Smoke: the hpa-only spec — every arm in it is honestly wired today.
    (cd eval && python -m harness.runner experiments/phase7_smoke.yaml --workers 1)
    echo "== done — results in eval/results/phase7_smoke.duckdb =="
fi
