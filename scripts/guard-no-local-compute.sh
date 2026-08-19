#!/usr/bin/env bash
# Refuse to run heavy local work while a soak holds the machine.
#
# WP14 attempt 4: a `go test ./internal/operator/controllers/` on this laptop
# compiled the operator package and its Kubernetes dependencies during the
# soak, and the port-forward restart cascade began within two minutes. The
# justification at the time -- "editing a chart cannot affect a deployed
# cluster" -- was true and irrelevant: the file edits were harmless, the BUILD
# was not. A 24 h soak on a single machine has no CPU, memory or I/O isolation
# from the session observing it.
#
# The OOM cycle was later shown to be a system defect that did not need an
# observer (attempt 3 hit it with nothing else running), so this guard is not
# the fix for that. It is the fix for the observer being able to accelerate it
# while believing it was being careful.
#
# Usage:
#   scripts/guard-no-local-compute.sh            # exit 1 if a soak is running
#   scripts/guard-no-local-compute.sh go test ./...   # run only if clear
#
# Wire it into a pre-commit hook, or in front of any build:
#   scripts/guard-no-local-compute.sh go build ./...

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="$REPO/.soak-running"

if [ -f "$MARKER" ]; then
  echo "REFUSED: a load window is open." >&2
  sed 's/^/  /' "$MARKER" >&2
  echo >&2
  echo "  Heavy local work competes with the run for CPU and I/O on this" >&2
  echo "  machine. Wait for the marker to clear, or override deliberately" >&2
  echo "  with POLYFORGE_ALLOW_LOCAL_COMPUTE=1 and disclose it in the record." >&2
  [ "${POLYFORGE_ALLOW_LOCAL_COMPUTE:-}" = "1" ] || exit 1
  echo "  POLYFORGE_ALLOW_LOCAL_COMPUTE=1 set: proceeding under protest." >&2
fi

[ "$#" -eq 0 ] && exit 0
exec "$@"
