#!/usr/bin/env bash
# Phase 7 environment bootstrap (runs as the devcontainer postCreateCommand).
# Installs the two cluster-backend tools no devcontainer feature provides
# (kind, k6) and the harness's Python dependencies. Idempotent.
set -euo pipefail

KIND_VERSION="v0.29.0"
K6_VERSION="v0.57.0"

if ! command -v kind >/dev/null; then
    curl -sLo /tmp/kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64"
    sudo install /tmp/kind /usr/local/bin/kind
fi

if ! command -v k6 >/dev/null; then
    curl -sL "https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-amd64.tar.gz" \
        | tar -xz -C /tmp
    sudo install "/tmp/k6-${K6_VERSION}-linux-amd64/k6" /usr/local/bin/k6
fi

pip install -r eval/requirements.txt

echo "phase7 bootstrap done; preflight:"
cd eval && python -c "from harness import cluster_backend; missing = cluster_backend.preflight(); print('missing:', missing or 'nothing — ready')"
