#!/usr/bin/env bash
# B1 tier substrate on a rented GPU host (PREREG_WAVE4_LIVE_PLANE.md,
# session-44 amendment: single host, three tiers, vLLM).
#
# Brings up small/mid/large as three vLLM servers behind OpenAI-compatible
# endpoints, proves each is resident on the GPU with no CPU offload, and
# prints the exact POLYFORGE_EVAL_TIER_BACKENDS line the harness needs.
#
#   ./scripts/b1_tier_host.sh up       # install, serve, verify, print export
#   ./scripts/b1_tier_host.sh verify   # re-run the checks against live servers
#   ./scripts/b1_tier_host.sh down     # stop the servers
#
# VRAM: fp16 weights are ~1 + 6 + 15 = 22 GB before any KV cache, so a 24 GB
# card cannot hold all three with room to serve. 40 GB (A100) or better.
set -euo pipefail
# pip installs the vllm CLI into ~/.local/bin, which a fresh host's PATH does
# not include; without this `command -v vllm` fails and the script falls to
# the module path. Session 48 on g6e.2xlarge: six paid hours of "small never
# became ready" for exactly that.
export PATH="$HOME/.local/bin:$PATH"

SMALL_MODEL="${SMALL_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}"
MID_MODEL="${MID_MODEL:-Qwen/Qwen2.5-3B-Instruct}"
LARGE_MODEL="${LARGE_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
SMALL_PORT="${SMALL_PORT:-9101}"
MID_PORT="${MID_PORT:-9102}"
LARGE_PORT="${LARGE_PORT:-9103}"
# Fractions of one card. They must sum below 1.0; vLLM preallocates KV cache
# from its share, so the large tier needs the biggest slice. Measured on an
# L40S (44.4 GiB), session 48: 0.08/0.24 left small and mid with "No
# available memory for the cache blocks" -- weights plus the activation
# profile for vLLM's default 32k context ate the whole share. The eval's
# prompts are tens of tokens, so the context is capped (SERVE_ARGS) and the
# shares rebalanced; all three tiers run with the same serving arguments.
SMALL_UTIL="${SMALL_UTIL:-0.12}"
MID_UTIL="${MID_UTIL:-0.26}"
LARGE_UTIL="${LARGE_UTIL:-0.58}"
SERVE_ARGS="${SERVE_ARGS:---max-model-len 4096 --max-num-seqs 128}"
LOG_DIR="${LOG_DIR:-/tmp/b1-tiers}"
PID_FILE="$LOG_DIR/pids"
# The address the AI gateway will dial. The gateway is a POD inside kind, and
# from a pod 127.0.0.1 is the pod itself -- so the export line must carry the
# host's own address, which kind's nodes route to over the docker bridge.
# Measured in the session-48 dry run on an EC2 host: the mock was reachable
# from a container on the host's primary IP and on nothing else. The servers
# bind 0.0.0.0 for the same reason (uvicorn's default is loopback only).
ADVERTISE_HOST="${ADVERTISE_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
ADVERTISE_HOST="${ADVERTISE_HOST:-127.0.0.1}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "FATAL: $1 not found" >&2; exit 1; }; }

# Fail fast, before three model loads burn paid minutes. Session 44 spent five
# Kaggle attempts on install problems a ten-second check would have named: a
# CUDA-13 wheel against a CUDA-12.8 runtime, a torch ABI mismatch, and a
# transformers floor rather than a pin.
preflight() {
  echo "== preflight =="
  python - <<'PY'
import sys
try:
    import torch
except Exception as e:
    sys.exit(f"FATAL: torch will not import: {e}")
if not torch.cuda.is_available():
    sys.exit("FATAL: no CUDA device visible")
n = torch.cuda.device_count()
caps = [torch.cuda.get_device_capability(i) for i in range(n)]
tot = 0.0
for i, (maj, mn) in enumerate(caps):
    g = torch.cuda.get_device_properties(i).total_memory / 2**30
    tot += g
    print(f"  gpu{i}: {torch.cuda.get_device_name(i)}  cc {maj}.{mn}  {g:.1f} GiB")
if min(c[0] for c in caps) < 8:
    print("")
    print("  STOP: compute capability < 8.0. vLLM's fast paths (FlashAttention 2,")
    print("  FlashInfer, CUDA graphs) are gated on sm_80+ and fall back silently.")
    print("  Session 44 measured 6.84 rps on sm_75 T4s against the ~64 rps needed.")
    print("  This host cannot serve B1's frozen cell. Pick an A100 or L40S.")
    sys.exit(2)
if tot < 38:
    sys.exit(f"FATAL: {tot:.1f} GiB total VRAM. Three tiers need ~22 GiB of weights "
             "plus KV cache; pick a 40 GiB+ host.")
try:
    import vllm
    print(f"  vllm {vllm.__version__} imports OK")
except Exception as e:
    print(f"FATAL: vllm will not import: {e}")
    print("  If this is a CUDA/ABI mismatch, install a build for THIS runtime and")
    print("  pin transformers with it:")
    print("    pip uninstall -y torch torchvision torchaudio")
    print("    pip install vllm==0.11.0 transformers==4.56.2")
    print("  then re-run. Evidence: research/calibration/kernels/polyforge-vllm-probe/")
    sys.exit(1)
PY
  echo
}

serve_one() {
  local name="$1" model="$2" port="$3" util="$4"
  echo "  starting $name  $model  :$port  gpu-util=$util"
  # `vllm serve` is the supported CLI; the module path is the older spelling
  # and is absent from recent releases. Prefer the CLI, keep the fallback.
  if command -v vllm >/dev/null 2>&1; then
    # shellcheck disable=SC2086  # SERVE_ARGS is a word list by design
    nohup vllm serve "$model" --served-model-name "$name" \
      --host 0.0.0.0 --port "$port" --gpu-memory-utilization "$util" $SERVE_ARGS \
      >"$LOG_DIR/$name.log" 2>&1 &
    echo "$!" >>"$PID_FILE"; return
  fi
  nohup python -m vllm.entrypoints.openai.api_server \
    --model "$model" --served-model-name "$name" \
    --host 0.0.0.0 --port "$port" --gpu-memory-utilization "$util" $SERVE_ARGS \
    >"$LOG_DIR/$name.log" 2>&1 &
  echo "$!" >>"$PID_FILE"
}

wait_ready() {
  local name="$1" port="$2" tries=0
  printf "  waiting for %s " "$name"
  until curl -sf "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; do
    tries=$((tries + 1))
    # 7B weights download on first run; allow a generous ceiling.
    if [ "$tries" -gt 180 ]; then
      echo " TIMEOUT"; echo "FATAL: $name never became ready; see $LOG_DIR/$name.log" >&2; exit 1
    fi
    printf "."; sleep 10
  done
  echo " ready"
}

# The amendment's binding obligation: a CPU-offloaded tier measures the
# offload, not the tier. vLLM does not offload unless told to, so the check is
# that every server is up, answers, and the card actually holds the weights.
verify() {
  echo "== placement / residency check =="
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv
    echo
    nvidia-smi --query-compute-apps=pid,used_memory --format=csv
  else
    echo "  WARNING: nvidia-smi absent; cannot evidence GPU residency" >&2
  fi
  echo
  local fail=0
  for pair in "small:$SMALL_PORT" "mid:$MID_PORT" "large:$LARGE_PORT"; do
    local name="${pair%%:*}" port="${pair##*:}"
    local got
    got=$(curl -sf "http://127.0.0.1:$port/v1/models" 2>/dev/null || true)
    if [ -z "$got" ]; then echo "  $name: DOWN"; fail=1; continue; fi
    # A real completion, not just a liveness ping: an endpoint that lists a
    # model but cannot decode would pass a /v1/models check and void WL-H2.
    local reply
    reply=$(curl -sf "http://127.0.0.1:$port/v1/chat/completions" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"$name\",\"messages\":[{\"role\":\"user\",\"content\":\"2 + 2 =\"}],\"max_tokens\":8,\"temperature\":0}" \
      2>/dev/null || true)
    if echo "$reply" | grep -q '"content"'; then
      echo "  $name: OK (decoded)"
    else
      echo "  $name: LISTED BUT WILL NOT DECODE"; fail=1
    fi
    grep -iE "cpu offload|offloading|swap" "$LOG_DIR/$name.log" 2>/dev/null \
      | head -3 | sed "s/^/    $name log: /" || true
  done
  [ "$fail" -eq 0 ] || { echo; echo "FATAL: substrate not adequate; do NOT score." >&2; exit 1; }
  echo
  echo "== export line for the harness =="
  cat <<EXPORT
export POLYFORGE_EVAL_TIER_BACKENDS='{"small":{"kind":"openai","base_url":"http://$ADVERTISE_HOST:$SMALL_PORT/v1","model":"small"},"mid":{"kind":"openai","base_url":"http://$ADVERTISE_HOST:$MID_PORT/v1","model":"mid"},"large":{"kind":"openai","base_url":"http://$ADVERTISE_HOST:$LARGE_PORT/v1","model":"large"}}'
EXPORT
}


# --- split-host support -------------------------------------------------
# The runbook's single-host route needs no tunnel: the cluster and the tiers
# share a box, which is what makes session-33 clauses 2-3 void by its own
# clause 4. On the SPLIT-host route the cluster stays on the operator's own
# machine and only the GPU is rented, so each tier needs a public URL.
#
# A cloudflared quick tunnel exposes ONE port, and vLLM serves ONE model per
# server, so three tiers need three tunnels. Free, no account, no card.
get_cloudflared() {
  [ -x ./cloudflared ] && return 0
  echo "  fetching cloudflared ..."
  curl -sSL -o cloudflared     https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64     || { echo "FATAL: cloudflared download failed" >&2; return 1; }
  chmod +x cloudflared
}

tunnel_one() {
  local name="$1" port="$2" log="$LOG_DIR/tunnel-$name.log"
  ./cloudflared tunnel --url "http://127.0.0.1:$port" --no-autoupdate     >"$log" 2>&1 &
  echo "$!" >>"$PID_FILE"
  local tries=0 url=""
  while [ "$tries" -lt 60 ]; do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" 2>/dev/null | head -1)
    [ -n "$url" ] && { echo "$url"; return 0; }
    tries=$((tries + 1)); sleep 2
  done
  echo "FATAL: no tunnel URL for $name after 120s; see $log" >&2
  return 1
}

tunnels() {
  get_cloudflared || return 1
  echo "== opening one tunnel per tier =="
  local us um ul
  us=$(tunnel_one small "$SMALL_PORT") || return 1
  um=$(tunnel_one mid   "$MID_PORT")   || return 1
  ul=$(tunnel_one large "$LARGE_PORT") || return 1
  echo "  small $us"
  echo "  mid   $um"
  echo "  large $ul"
  echo
  echo "== export line for the harness (SPLIT-HOST) =="
  cat <<EXPORT
export POLYFORGE_EVAL_TIER_BACKENDS='{"small":{"kind":"openai","base_url":"$us/v1","model":"small"},"mid":{"kind":"openai","base_url":"$um/v1","model":"mid"},"large":{"kind":"openai","base_url":"$ul/v1","model":"large"}}'
EXPORT
  echo
  echo "  NOTE: session-33 clauses 2-3 APPLY on this route. Run"
  echo "  eval/scripts/tunnel_preflight.py before the cluster half -- it checks"
  echo "  the tier gap survives the round-trip AND that the slowest tier stays"
  echo "  inside the 2500 ms premium SLO. Over target voids the run."
}

case "${1:-up}" in
  up)
    need python; need curl
    mkdir -p "$LOG_DIR"; : >"$PID_FILE"
    python -c "import vllm" 2>/dev/null || { echo "installing vllm..."; pip install -q vllm; }
    preflight
    echo "== starting three tiers (sequentially) =="
    # One at a time, each ready before the next starts. Started together on
    # one card (session 48, L40S), every engine's memory profile counted the
    # other two's growing allocations as its own overhead and refused with
    # "No available memory for the cache blocks" -- at 0.58 of the card for a
    # 15 GiB model. Whichever finished profiling first survived.
    serve_one small "$SMALL_MODEL" "$SMALL_PORT" "$SMALL_UTIL"; wait_ready small "$SMALL_PORT"
    serve_one mid   "$MID_MODEL"   "$MID_PORT"   "$MID_UTIL";   wait_ready mid   "$MID_PORT"
    serve_one large "$LARGE_MODEL" "$LARGE_PORT" "$LARGE_UTIL"; wait_ready large "$LARGE_PORT"
    echo; verify ;;
  tunnels) tunnels ;;
  up-split) "$0" up && tunnels ;;
  verify) verify ;;
  down)
    [ -f "$PID_FILE" ] && while read -r pid; do kill "$pid" 2>/dev/null || true; done <"$PID_FILE"
    echo "stopped" ;;
  *) echo "usage: $0 {up|up-split|tunnels|verify|down}" >&2; exit 2 ;;
esac
