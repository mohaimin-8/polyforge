#!/usr/bin/env bash
# Sample the cluster while a soak stage runs, so a stall has an explanation.
#
# WHY THIS EXISTS. Stage B aborted at t+3161s on 26 dropped iterations. The
# generator's pool was the proximate cause and is fixed (cluster_backend
# k6_script), but the pool only ran out because something in the system under
# test stalled for seconds — and NOTHING recorded what. The whole run left four
# files behind, none of which can say whether a pod restarted, whether
# PostgreSQL was checkpointing, or whether the WSL2 VM was reclaiming memory.
#
# That is the same hole that made attempts 1-4 diagnosable only in hindsight,
# and always after the next 24-hour run. Phase 2.3 of the remediation roadmap
# asks for the run to be observable WHILE it runs; this is the cluster half of
# it. (The roadmap also asks for k6 `--out csv`. Deliberately not used: it
# writes a row per metric per iteration, which is ~1 GB for this stage and
# ~25 GB for the 24 h sitting. The 60 s buckets eval-export now emits carry the
# same timeline server-side, at a size that survives.)
#
#   scripts/soak_observer.sh [interval_s] &
#
# Every sample is a CSV row; a sample that cannot be taken writes empty fields
# rather than stopping. This must never be the reason a run fails.

set -uo pipefail
NS=polyforge
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE="${EVIDENCE_DIR:-$REPO/eval/results/soak_stage_b_evidence}"
INTERVAL=${1:-20}
mkdir -p "$EVIDENCE" 2>/dev/null || true
OUT="$EVIDENCE/observer.csv"

# Cumulative counters, so a spike between two samples is still visible as a
# delta even though the sample itself missed the moment.
echo "epoch,iso,nodes_mem_pct,pods_ready,pods_total,restarts_total,max_pod_mem_mi,cp_replicas,pg_ckpt_timed,pg_ckpt_req,pg_ckpt_write_ms,pg_ckpt_sync_ms,pg_rows_ingested,pg_autovacuum,kind_node_mem" > "$OUT"

psql_scalar() { # SQL -> single value, empty on any failure
  kubectl --namespace "$NS" exec deploy/postgres -- \
    psql -U postgres -d polyforge -tAc "$1" 2>/dev/null | tr -d '\r' | head -1
}

while true; do
  epoch=$(date +%s)
  iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Node memory as a percentage of allocatable: the WSL2 VM's ceiling is the
  # one resource this box is actually short of.
  nodes_mem_pct=$(kubectl top nodes --no-headers 2>/dev/null |
    awk '{gsub(/%/,"",$5); if ($5+0 > m) m = $5+0} END {printf "%d", m+0}')

  pods=$(kubectl --namespace "$NS" get pods --no-headers 2>/dev/null)
  pods_total=$(printf '%s\n' "$pods" | grep -c . )
  pods_ready=$(printf '%s\n' "$pods" | awk '$2 ~ /^([0-9]+)\/\1$/ && $3 == "Running"' | grep -c .)
  # Restart count is the single number that would have settled attempt 4's
  # "did the pods OOM or did the load path die" question on the spot.
  restarts_total=$(printf '%s\n' "$pods" | awk '{s += $4} END {printf "%d", s+0}')
  max_pod_mem_mi=$(kubectl --namespace "$NS" top pods --no-headers 2>/dev/null |
    awk '{gsub(/Mi/,"",$3); if ($3+0 > m) m = $3+0} END {printf "%d", m+0}')
  cp_replicas=$(kubectl --namespace "$NS" get deploy polyforge-control-plane \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null)

  # PostgreSQL: checkpoints and their write/sync time are the standard
  # explanation for a multi-second write stall on slow storage, and pgdata is
  # an emptyDir on a VHDX here. In PG16 these live in pg_stat_bgwriter; in
  # PG17+ they moved to pg_stat_checkpointer, so try both.
  ckpt=$(psql_scalar "SELECT num_timed||','||num_requested||','||write_time::bigint||','||sync_time::bigint FROM pg_stat_checkpointer")
  if [ -z "$ckpt" ]; then
    ckpt=$(psql_scalar "SELECT checkpoints_timed||','||checkpoints_req||','||checkpoint_write_time::bigint||','||checkpoint_sync_time::bigint FROM pg_stat_bgwriter")
  fi
  pg_ckpt_timed=$(echo "$ckpt" | cut -d, -f1)
  pg_ckpt_req=$(echo "$ckpt" | cut -d, -f2)
  pg_ckpt_write_ms=$(echo "$ckpt" | cut -d, -f3)
  pg_ckpt_sync_ms=$(echo "$ckpt" | cut -d, -f4)
  # n_tup_ins, not count(*): the row count is the point, but a sequential scan
  # every 20 s would make the observer part of the load it is measuring.
  pg_rows_ingested=$(psql_scalar "SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname = 'telemetry_events'")
  pg_autovacuum=$(psql_scalar "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'autovacuum worker'")

  # Container-level memory for the kind nodes, which is what the VM ceiling
  # actually bites on.
  kind_node_mem=$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null |
    awk '/control-plane|worker/ {print $1"="$2}' | paste -sd'|' -)

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$epoch" "$iso" "$nodes_mem_pct" "$pods_ready" "$pods_total" \
    "$restarts_total" "$max_pod_mem_mi" "$cp_replicas" \
    "$pg_ckpt_timed" "$pg_ckpt_req" "$pg_ckpt_write_ms" "$pg_ckpt_sync_ms" \
    "$pg_rows_ingested" "$pg_autovacuum" "$kind_node_mem" >> "$OUT"

  sleep "$INTERVAL"
done
