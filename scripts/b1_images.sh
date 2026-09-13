#!/usr/bin/env bash
# Build the four polyforge images and pull the three third-party ones that
# eval/harness/cluster_backend.py side-loads into every per-run kind cluster.
#
# Neither `docker save` nor `kind load docker-image` pulls or builds, and no
# runbook step did either: on the laptop the images have existed since
# phase 7, so a fresh rented host would fail at the side-load step -- after
# the box is paid for, before the step-1c mock probe. execute() now refuses
# up front and names this script.
#
# Idempotent. Run from anywhere; ~5 min on a fresh host (Go module download
# dominates). Usage: ./scripts/b1_images.sh [--verify-only]
set -euo pipefail
cd "$(dirname "$0")/.."

# Image names come from the harness constants, not a copy kept here. The
# tr strips the CR that Python's stdout carries on Windows, where this script
# is rehearsed; without it every tag ends in a carriage return and docker
# refuses it as an invalid reference.
readarray -t IMAGES < <(cd eval && python -c "
from harness import cluster_backend as cb
for img in (cb.CONTROL_PLANE_IMAGE, cb.GATEWAY_IMAGE, cb.OPERATOR_IMAGE,
            cb.PLANNER_IMAGE, cb.NATS_IMAGE, cb.POSTGRES_IMAGE,
            cb.METRICS_SERVER_IMAGE):
    print(img)
" | tr -d '\r')
CONTROL_PLANE="${IMAGES[0]}"; GATEWAY="${IMAGES[1]}"
OPERATOR="${IMAGES[2]}";      PLANNER="${IMAGES[3]}"
THIRD_PARTY=("${IMAGES[@]:4}")

if [[ "${1:-}" != "--verify-only" ]]; then
    echo "== build (control plane, gateway, operator, planner) =="
    docker build -t "$CONTROL_PLANE" .
    docker build -f Dockerfile.gateway  -t "$GATEWAY"  .
    docker build -f Dockerfile.operator -t "$OPERATOR" .
    docker build -f services/planner/Dockerfile -t "$PLANNER" .

    echo "== pull (side-loaded so kind nodes never reach Docker Hub) =="
    for img in "${THIRD_PARTY[@]}"; do
        docker pull --platform linux/amd64 "$img"
    done
fi

echo "== verify =="
missing=0
for img in "${IMAGES[@]}"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
        echo "  ok       $img"
    else
        echo "  MISSING  $img"; missing=1
    fi
done
if (( missing )); then
    echo "not ready: build or pull the images marked MISSING" >&2
    exit 1
fi
echo "all side-loaded images present"
