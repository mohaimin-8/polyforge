#!/usr/bin/env bash
# Phase 7 environment bootstrap (runs as the devcontainer postCreateCommand
# and as runbook section 1 on a rented host). Installs every cluster-backend
# tool the host lacks -- kind, k6, kubectl, helm -- plus the harness's Python
# dependencies. Idempotent.
#
# Measured on a fresh AWS DLAMI (Ubuntu 22.04) in the session-48 dry run: the
# image ships python3 and pip but no `python`, and neither kubectl nor helm.
# The devcontainer provides those three through its features, which is why
# this script never had to; a rented host has no features.
set -euo pipefail

KIND_VERSION="v0.29.0"
K6_VERSION="v0.57.0"

if ! command -v python >/dev/null; then
    sudo apt-get install -y -q python-is-python3
fi

if ! command -v kubectl >/dev/null; then
    KUBECTL_VERSION="$(curl -sL https://dl.k8s.io/release/stable.txt)"
    curl -sLo /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
    sudo install /tmp/kubectl /usr/local/bin/kubectl
fi

if ! command -v helm >/dev/null; then
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

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
