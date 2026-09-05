#!/usr/bin/env bash
# L6 verification: does soak_observer.sh's /metrics scrape actually capture
# polyforge_http_request_duration_seconds from a LIVE server under load?
# Offline the awk was checked against a synthesized payload; this runs it
# against the real binary's real exposition output.
set -uo pipefail
REPO="$1"; SCRATCH="$2"
BIN="$SCRATCH/control-plane.exe"
export POLYFORGE_ADMIN_KEY=pf_admin_l6
export POLYFORGE_DB_PATH="$SCRATCH/l6.db"
rm -f "$POLYFORGE_DB_PATH" 2>/dev/null

cd "$REPO"
go build -buildvcs=false -o "$BIN" ./cmd/control-plane || exit 1
"$BIN" > "$SCRATCH/cp.log" 2>&1 &
CP=$!
trap 'kill $CP 2>/dev/null' EXIT

for i in $(seq 1 30); do
  curl -fsS --max-time 2 http://localhost:8080/healthz >/dev/null 2>&1 && break
  sleep 1
done

KEY=$(curl -fsS -X POST http://localhost:8080/v1/tenants \
  -H "X-PolyForge-Admin-Key: $POLYFORGE_ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"id":"l6","name":"L6 verification"}' |
  python -c "import sys,json; print(json.load(sys.stdin)['bootstrap_api_key']['secret'])")
[ -z "$KEY" ] && { echo "FAIL: no tenant key"; exit 1; }

replay() {
  for i in $(seq 1 "$1"); do
    curl -fsS -o /dev/null -X POST http://localhost:8080/v1/tenants/l6/workloads/replay \
      -H "X-PolyForge-API-Key: $KEY" -H 'Content-Type: application/json' \
      -d '{"kind":"crud_read"}'
  done
}
replay 60
echo "first load done"

export EVIDENCE_DIR="$SCRATCH/evidence"
export METRICS_URL="http://localhost:8080/metrics"
rm -rf "$EVIDENCE_DIR"
bash "$REPO/scripts/soak_observer.sh" 2 > "$SCRATCH/observer.log" 2>&1 &
OBS=$!
sleep 4
replay 120           # load DURING observation, so the counters must move
sleep 5
kill $OBS 2>/dev/null
wait $OBS 2>/dev/null
echo "observer stopped"
