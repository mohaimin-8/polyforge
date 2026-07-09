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

TARGET="${1:-http://host.docker.internal:8080}"
mkdir -p artifacts

docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -v "$(pwd)/artifacts:/zap/wrk:rw" \
  ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t "$TARGET" -r zap-baseline.html -I

echo "report: artifacts/zap-baseline.html"
