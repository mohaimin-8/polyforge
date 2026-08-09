#!/usr/bin/env sh
# W24: OWASP ZAP baseline scan against a running PolyForge (passive rules
# plus spider; no active attack payloads, so safe against dev).
#
#   docker compose up -d
#   ./scripts/zap-baseline.sh http://host.docker.internal:8080
#
# Exit code is nonzero on WARN/FAIL findings; the report lands in
# artifacts/zap-baseline.html for triage into docs/SECURITY.md §Findings.
set -eu

#
# TWO MODES. The default baseline scan spiders from a URL, which on this
# service reaches almost nothing: the control plane is a JSON API with no
# root route (GET / is 404) and no sitemap, so the spider sees two 404s and
# every passive rule then "passes" against an empty surface. A clean baseline
# result is therefore NOT evidence the API is clean.
#
#   ./scripts/zap-baseline.sh                 # passive baseline (shallow)
#   ./scripts/zap-baseline.sh --api           # OpenAPI-driven scan (real coverage)
#
# --api imports api/openapi.yaml so ZAP requests the 20 declared paths
# instead of guessing. Note the endpoints still answer 401 without a
# credential, so this covers the unauthenticated surface — auth-behind rules
# need a ZAP context with a tenant API key, which is the next increment.
set -eu

MODE="baseline"
if [ "${1:-}" = "--api" ]; then
    MODE="api"
    shift
fi

TARGET="${1:-http://host.docker.internal:8080}"
mkdir -p artifacts

# Docker Desktop on Windows cannot mount an MSYS-style path: Git Bash's
# `pwd` returns /c/Users/... and the daemon needs C:/Users/.... Mounting the
# unconverted path does not fail loudly — the container starts with /zap/wrk
# absent, and zap-baseline.py then aborts with "a file based option has been
# specified but the directory '/zap/wrk' is not mounted", which reads like a
# ZAP misconfiguration rather than a path bug. `pwd -W` yields the Windows
# form under MSYS and does not exist elsewhere, so the fallback covers Linux
# and macOS unchanged.
HOSTDIR="$(pwd -W 2>/dev/null || pwd)"

# MSYS_NO_PATHCONV stops Git Bash rewriting the *container-side* path
# (/zap/wrk) into a Windows path when it crosses the argument boundary.
if [ "$MODE" = "api" ]; then
    # zap-api-scan.py reads the spec and requests every declared path, so
    # coverage comes from the contract rather than from spidering a JSON API
    # that exposes no links. The spec is copied into the mounted dir because
    # /zap/wrk is the only path the container can read.
    cp api/openapi.yaml artifacts/openapi.yaml
    MSYS_NO_PATHCONV=1 docker run --rm \
      --add-host=host.docker.internal:host-gateway \
      -v "${HOSTDIR}/artifacts:/zap/wrk:rw" \
      ghcr.io/zaproxy/zaproxy:stable \
      zap-api-scan.py -t /zap/wrk/openapi.yaml -f openapi \
      -O "$TARGET" -r zap-api.html -I
    echo "report: artifacts/zap-api.html"
else
    MSYS_NO_PATHCONV=1 docker run --rm \
      --add-host=host.docker.internal:host-gateway \
      -v "${HOSTDIR}/artifacts:/zap/wrk:rw" \
      ghcr.io/zaproxy/zaproxy:stable \
      zap-baseline.py -t "$TARGET" -r zap-baseline.html -I
    echo "report: artifacts/zap-baseline.html"
fi
