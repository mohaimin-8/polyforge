"""Serve the B1 model tiers from a free Kaggle GPU kernel (PREREG_WAVE4_LIVE_PLANE.md).

`kaggle_tier_bench.py` proved the tier models run on the free pool; this
serves them, so the GPU half of the three-knob live plane costs nothing. The
cluster half runs elsewhere (a free Codespace) and reaches this over a
tunnel — see `docs/WAVE4_FREE_ROUTE.md` for the whole route and for the
substrate deviation that split introduces.

One OpenAI-compatible endpoint serves *both* tiers, selected by the request's
`model` field, so the run needs one tunnel rather than one per tier:

    small -> Qwen/Qwen2.5-0.5B-Instruct   (model: qwen2.5-0.5b-instruct)
    mid   -> Qwen/Qwen2.5-3B-Instruct     (model: qwen2.5-3b-instruct)

The 7B `large` tier is deliberately absent. TIER_BENCH.md measured it
spilling to CPU on the free pool's 16 GB card in two independent sessions,
and the pre-registration's amendment rule covers exactly this: "if the host
cannot hold 7B in GPU memory, the large tier is dropped and the run is a
two-tier run, declared as such." Two tiers is protocol-legal; a CPU-offloaded
third tier would be a measurement of the offload, not of the tier.

Wire contract (matches internal/ai/gateway/openai.go, which is what will call
this): POST {base}/chat/completions with {"model", "messages", "stream"},
answering {"model", "choices":[{"message":{"role","content"},
"finish_reason"}], "usage":{"prompt_tokens","completion_tokens"}}.

    # on Kaggle (GPU + internet on), as a script kernel:
    python kaggle_tier_server.py

    # anywhere, to check the HTTP contract without a GPU:
    python kaggle_tier_server.py --mock --port 9109

Generation is greedy and fixed-length (48 new tokens, the same shape
`kaggle_tier_bench.py` measured) so per-tier latency stays comparable to the
committed tier table instead of varying with sampled output length.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import subprocess
import sys
import threading
import urllib.request
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_NEW_TOKENS = 48
TIERS = {
    "qwen2.5-0.5b-instruct": ("small", "Qwen/Qwen2.5-0.5B-Instruct"),
    "qwen2.5-3b-instruct": ("mid", "Qwen/Qwen2.5-3B-Instruct"),
    # Third tier restored session 44. The session-33 amendment dropped it
    # because a 16 GB card could not hold the 7B, and PREREG_WAVE4_LIVE_PLANE's
    # session-44 amendment supersedes that: measured on 2 x 16 GB with
    # `modules offloaded to cpu/disk: 0`.
    "qwen2.5-7b-instruct": ("large", "Qwen/Qwen2.5-7B-Instruct"),
}
# Mock delays approximate TIER_BENCH.md's measured means, so --mock exercises
# the same *shape* the WL-H2 tier probe looks for (a material small/mid gap)
# without a GPU. They are not measurements and never enter a record.
# `large` is only marginally above `mid` on purpose: the corrected tier
# bench (tier_bench_t4.csv, zero modules offloaded) measures the ratio at
# 1 : 1.475 : 1.518, not the 1 : 1.516 : 16.64 the offloaded P100 run
# implied. A mock with a large 7B gap would rehearse a shape the hardware
# does not have.
MOCK_DELAY_S = {"small": 1.24, "mid": 1.88, "large": 1.90}
# Scale the mock delays to simulate a faster substrate. Session 44 used this to
# test whether the tier-backend ceiling is what kills `joint_stress`: the
# defaults above are P100/T4-era transformers timings, and BATCH_MAX/delay caps
# the server near 34 rps, below joint_stress's 64 rps base. It is a knob for
# DIAGNOSIS, not a measurement -- these delays never enter a record either way.
_scale = float(os.environ.get("POLYFORGE_MOCK_DELAY_SCALE", "1.0"))
if _scale != 1.0:
    MOCK_DELAY_S = {k: v * _scale for k, v in MOCK_DELAY_S.items()}

# Micro-batching (docs/ZERO_COST_ROADMAP.md Phase 2). Frozen at whatever
# TIER_BENCH_BATCHED.md certifies before the scored run; these are the
# starting values, not measured ones. The window is the latency a single
# request pays to make batching possible, and Amendment clause 3 leaves
# 619 ms of headroom over the `mid` tier, so 50 ms is well inside it --
# but it is re-measured under load, never assumed.
BATCH_MAX = 64
BATCH_WINDOW_MS = 50.0


def _ensure_torch_kernels() -> None:
    """The free pool may grant a P100 (sm_60); current torch wheels ship no
    sm_60 kernels. Same fallback `kaggle_tier_bench.py` needed, kept
    identical so the serving path matches the benched path."""
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("no GPU granted — rerun the kernel with GPU enabled")
    if torch.cuda.get_device_capability(0)[0] >= 7 or os.environ.get("TIER_SERVER_REEXEC"):
        return
    print(f"pre-sm_70 GPU granted: {torch.cuda.get_device_name(0)} — "
          "installing cu118 torch and re-executing", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "torch==2.4.1",
                    "--index-url", "https://download.pytorch.org/whl/cu118"], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "transformers==4.46.3", "accelerate==1.1.1"], check=True)
    # torchvision/torchaudio are ABI-bound to the newer torch; transformers
    # treats an absent torchvision as fine and a broken one as fatal.
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-q", "-y",
                    "torchvision", "torchaudio"], check=False)
    os.environ["TIER_SERVER_REEXEC"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])


@dataclass
class _Job:
    """One in-flight request waiting for its batch to decode."""
    messages: list
    event: threading.Event = field(default_factory=threading.Event)
    result: tuple | None = None
    error: BaseException | None = None


def pad_batch(tok, texts: list[str]):
    """Tokenise a batch with LEFT padding.

    Decoder-only generation continues from the LAST position, so a right-padded
    row would continue from PAD instead of from the end of its prompt and come
    back fluent and wrong -- a silent corruption, not an error. Left padding
    plus the attention mask is what makes a batched decode token-identical to
    the serial one, and `test_batched_generation_matches_serial_exactly` is the
    assertion that keeps it that way.
    """
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    previous = tok.padding_side
    tok.padding_side = "left"
    try:
        return tok(texts, return_tensors="pt", padding=True)
    finally:
        tok.padding_side = previous


class TierModels:
    """Both tiers resident, each served by a micro-batching worker.

    **Why this is not a per-tier lock any more.** It was, and the lock was
    defended as "the honest behaviour of a single card". Measured on the free
    route that gave **0.5 req/s**, while the `wave4_live_plane` cell needs ~64
    AI rps at base (8 tenants x 8 AI rps) and ~110 at burst peak -- so the
    scored matrix died at 48.3% failed requests (docs/WAVE4_FREE_ROUTE.md 0a).
    That 0.5 req/s is a batch-size-1 SOFTWARE limit, not a P100 limit: decode
    is memory-bandwidth-bound, Qwen2.5-3B in fp16 is ~6 GB against 549-732
    GB/s, so 48 tokens should cost ~400-530 ms and TIER_BENCH.md measured
    1881 ms. A batched step reads those weights ONCE for the whole batch.

    **Why batching is safe here specifically.** `min_new_tokens ==
    max_new_tokens == 48` with `do_sample=False`, so every request decodes
    exactly 48 greedy steps: no EOS variance, no ragged tail, nothing to stall
    the batch on its slowest member. That constraint was already in place for
    an unrelated reason (stopping the prompt from setting the measurement).

    Substrate character changes, and is disclosed: docs/ZERO_COST_ROADMAP.md
    section 4. The prereg's Amendment clause 2 already forbids quoting live
    absolutes from this route as tier latencies, and clause 3 (slowest tier
    inside the 2500 ms premium AI SLO) is re-measured under load before
    anything is scored.
    """

    def __init__(self, mock: bool = False, batch_max: int = BATCH_MAX,
                 batch_window_ms: float = BATCH_WINDOW_MS):
        self.mock = mock
        self.models: dict[str, tuple] = {}
        self.batch_max = max(1, batch_max)
        self.batch_window_s = max(0.0, batch_window_ms) / 1000.0
        self.queues: dict[str, queue.Queue] = {
            tier: queue.Queue() for tier, _ in TIERS.values()}
        self._stop = threading.Event()
        self.workers: dict[str, threading.Thread] = {}
        if mock:
            print("mock mode: no models loaded", flush=True)
        else:
            _ensure_torch_kernels()
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            for alias, (tier, repo) in TIERS.items():
                print(f"loading {tier} <- {repo} ...", flush=True)
                tok = AutoTokenizer.from_pretrained(repo)
                model = AutoModelForCausalLM.from_pretrained(
                    repo, torch_dtype=torch.float16, device_map="cuda:0")
                model.eval()
                self.models[tier] = (tok, model)
                print(f"  {tier} ready", flush=True)
        for tier in self.queues:
            worker = threading.Thread(target=self._serve_tier, args=(tier,),
                                      daemon=True, name=f"batch-{tier}")
            worker.start()
            self.workers[tier] = worker

    def close(self) -> None:
        self._stop.set()

    def generate(self, tier: str, messages: list[dict]) -> tuple[str, int, int]:
        """Blocking, exactly as before. The caller cannot tell it was batched."""
        job = _Job(messages=messages)
        self.queues[tier].put(job)
        job.event.wait()
        if job.error is not None:
            raise job.error
        return job.result

    def _serve_tier(self, tier: str) -> None:
        """Collect up to `batch_max` jobs or wait `batch_window_s`, then decode.

        The window is measured from the arrival of the FIRST job and is not
        reset by later arrivals, so a steady stream cannot starve the batch
        that is already waiting.
        """
        q = self.queues[tier]
        while not self._stop.is_set():
            try:
                batch = [q.get(timeout=0.1)]
            except queue.Empty:
                continue
            deadline = time.monotonic() + self.batch_window_s
            while len(batch) < self.batch_max:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(q.get(timeout=remaining))
                except queue.Empty:
                    break
            try:
                results = self._generate_batch(tier, [j.messages for j in batch])
            except BaseException as err:  # noqa: BLE001 -- every waiter must wake
                for job in batch:
                    job.error = err
                    job.event.set()
                continue
            for job, result in zip(batch, results):
                job.result = result
                job.event.set()

    def _generate_batch(self, tier: str,
                        batch: list[list[dict]]) -> list[tuple[str, int, int]]:
        """One padded `generate` for the whole batch."""
        if self.mock:
            # One sleep for the batch, not one per request: the mock has to
            # have the throughput SHAPE of the real batched server, or the
            # queue semantics above would be tested against a substrate that
            # cannot exhibit them.
            time.sleep(MOCK_DELAY_S.get(tier, 1.0))
            return [(f"[mock {tier} reply]", 16, MAX_NEW_TOKENS) for _ in batch]
        import torch

        tok, model = self.models[tier]
        texts = [tok.apply_chat_template(m, tokenize=False,
                                         add_generation_prompt=True)
                 for m in batch]
        inputs = pad_batch(tok, texts).to(model.device)
        with torch.no_grad():
            # min == max: every request decodes exactly MAX_NEW_TOKENS,
            # ignoring EOS. TIER_BENCH.md timed "a fixed 48-token completion",
            # and matching that is what makes per-tier latency comparable to
            # the committed table. It is also what makes the batch uniform.
            #
            # It also stops the *prompt* from setting the measurement. Left
            # free to stop early, a prompt like "reply with the word ok" ends
            # after ~3 tokens on both tiers, and the small/mid gap collapses
            # from ~660 ms to ~18 ms -- which reads exactly like an inert tier
            # knob and would fail WL-H2 for a reason that is an artifact of the
            # prompt, not the substrate.
            out = model.generate(**inputs,
                                 max_new_tokens=MAX_NEW_TOKENS,
                                 min_new_tokens=MAX_NEW_TOKENS,
                                 do_sample=False,
                                 pad_token_id=tok.pad_token_id or tok.eos_token_id)
        width = int(inputs["input_ids"].shape[1])
        results = []
        for i in range(len(batch)):
            # The real prompt length is the unmasked part; `width` includes
            # this row's left padding and would over-report every short prompt.
            prompt_tokens = int(inputs["attention_mask"][i].sum())
            new_tokens = out[i][width:]
            results.append((tok.decode(new_tokens, skip_special_tokens=True),
                            prompt_tokens, int(new_tokens.shape[0])))
        return results


def make_handler(models: TierModels, auth_token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _authorized(self) -> bool:
            """A quick tunnel puts this server on the public internet behind
            nothing but a random subdomain. That is obscurity, not access
            control, and the thing behind it spends someone's GPU. When a
            token is configured every /v1/* route requires it; /healthz stays
            open so the tunnel and the orchestrator can probe liveness."""
            if auth_token is None:
                return True
            scheme, _, presented = self.headers.get("Authorization", "").partition(" ")
            return (scheme.lower() == "bearer"
                    and hmac.compare_digest(presented.strip(), auth_token))

        def _deny(self) -> None:
            # Drain first: replying to a POST without consuming its body makes
            # the peer see a connection reset instead of the 401.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, 1 << 20))
            body = json.dumps({"error": "unauthorized"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/healthz", "/v1/healthz"):
                self._send(200, {"status": "ok", "mock": models.mock,
                                 "tiers": sorted({t for t, _ in TIERS.values()})})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                self._deny()
                return
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._drain()
                self._send(404, {"error": "not found"})
                return
            try:
                payload = json.loads(self._drain() or b"{}")
                alias = str(payload.get("model", "")).lower()
                if alias not in TIERS:
                    raise ValueError(
                        f"unknown model {alias!r}; serve one of {sorted(TIERS)}")
                messages = payload.get("messages") or []
                if not isinstance(messages, list) or not messages:
                    raise ValueError("messages must be a non-empty list")
                tier, _ = TIERS[alias]
                reply, ptok, ctok = models.generate(
                    tier, [{"role": m.get("role", "user"),
                            "content": m.get("content", "")} for m in messages])
            except (ValueError, KeyError, TypeError) as err:
                self._send(400, {"error": str(err)})
                return
            except Exception as err:  # a decode failure must not kill the server
                self._send(500, {"error": f"{type(err).__name__}: {err}"})
                return
            self._send(200, {
                "id": f"chatcmpl-{int(time.time()*1000)}",
                "object": "chat.completion",
                "model": alias,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": reply}}],
                "usage": {"prompt_tokens": ptok, "completion_tokens": ctok,
                          "total_tokens": ptok + ctok},
            })

        def _drain(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return b""
            return self.rfile.read(min(length, 1 << 20)) if length > 0 else b""

    return Handler


class TierHTTPServer(ThreadingHTTPServer):
    """socketserver's listen backlog is 5. The gateway keeps two idle upstream
    connections per host (Go's default) and opens fresh ones for the rest, so
    at ~100 AI rps the accept queue overflowed and the gateway answered 502
    in 2 ms -- 98 times in the session-48 rung-1 probe, the substrate
    stand-in failing a guard meant for the cluster. vLLM (uvicorn) listens
    with a backlog of 2048; the mock must not be the weaker of the two."""
    request_queue_size = 2048
    daemon_threads = True


def primary_host_ip() -> str:
    """The address this host has on its default route -- what a kind pod can
    dial to reach a server bound here on 0.0.0.0. A UDP socket to a public
    address is never sent; connect() only selects the local interface."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # TEST-NET-1: nothing is sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def start_tunnel(port: int) -> str | None:
    """Free cloudflared quick tunnel — no account, no card. Returns the public
    URL, or None if cloudflared could not be obtained (the run can still
    proceed if the cluster reaches this host some other way)."""
    binary = "./cloudflared"
    if not os.path.exists(binary):
        url = ("https://github.com/cloudflare/cloudflared/releases/latest/"
               "download/cloudflared-linux-amd64")
        rc = subprocess.run(["curl", "-sSL", "-o", binary, url]).returncode
        if rc != 0:
            print("cloudflared download failed; no tunnel", flush=True)
            return None
        os.chmod(binary, 0o755)
    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if "trycloudflare.com" in line:
            for token in line.split():
                if token.startswith("https://") and "trycloudflare.com" in token:
                    return token.strip()
    print("tunnel URL not seen within 60s", flush=True)
    return None


def announce(endpoint: str, payload: dict, attempts: int = 5) -> bool:
    """Publish the tunnel URL outward, because it cannot be read inward.

    Kaggle script kernels are batch: the API returns no output at all while a
    kernel runs, and the full log only after it exits — by which time a server
    kernel is gone. Verified, not assumed (a probe kernel printed a line at
    t=1.3 s and it was invisible until COMPLETE). So the kernel announces
    itself to a rendezvous the orchestrator is already watching, inverting the
    direction of discovery.

    Failure here is not fatal: the URL is still printed to the log for a human
    reading the notebook, which is the manual path.
    """
    body = json.dumps(payload).encode()
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                endpoint, data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                if 200 <= resp.status < 300:
                    print(f"announced to {endpoint}", flush=True)
                    return True
        except Exception as err:  # any transport problem is retryable
            print(f"announce attempt {attempt + 1} failed: {err}", flush=True)
        time.sleep(3 * (attempt + 1))
    print("could not announce; the URL above is the manual path", flush=True)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9109)
    ap.add_argument("--mock", action="store_true",
                    help="skip model loading; canned replies with tier-shaped "
                         "delays, for checking the HTTP contract off-GPU")
    ap.add_argument("--no-tunnel", action="store_true")
    ap.add_argument("--advertise-host", default="",
                    help="with --no-tunnel: the address the AI gateway should "
                         "dial. The gateway is a pod inside kind, where "
                         "127.0.0.1 is the pod itself, so this defaults to the "
                         "host's primary IP (session-48 dry run)")
    ap.add_argument("--hours", type=float, default=8.0,
                    help="serve for this long, then exit cleanly (Kaggle "
                         "kernels are time-boxed; leaving early frees quota)")
    ap.add_argument("--auth-token", default=os.environ.get("TIER_SERVER_TOKEN", ""),
                    help="bearer token required on /v1/*; strongly recommended "
                         "whenever a tunnel is open, since the tunnel makes this "
                         "server publicly reachable")
    ap.add_argument("--announce-url", default=os.environ.get("TIER_SERVER_ANNOUNCE", ""),
                    help="POST the tunnel URL here once it is up, so an "
                         "orchestrator can discover a kernel whose log it "
                         "cannot read while it runs")
    args = ap.parse_args()

    auth_token = args.auth_token.strip() or None
    models = TierModels(mock=args.mock)
    server = TierHTTPServer(("0.0.0.0", args.port),
                            make_handler(models, auth_token))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"tier server listening on :{args.port}", flush=True)

    public = None if args.no_tunnel else start_tunnel(args.port)
    if args.no_tunnel:
        # Single-host route: the export line was never printed here, so the
        # runbook's "export the mock's printed line" had nothing to copy, and
        # a hand-written 127.0.0.1 would not reach the host from a pod.
        host = args.advertise_host or primary_host_ip()
        backends = {
            tier: {"kind": "openai", "base_url": f"http://{host}:{args.port}/v1",
                   "model": alias}
            for alias, (tier, _) in TIERS.items()
        }
        print("\nExport this on the SAME host, then run the step-1c probe:\n", flush=True)
        print(f"export POLYFORGE_EVAL_TIER_BACKENDS='{json.dumps(backends)}'",
              flush=True)
        print(f"(advertising {host}; override with --advertise-host)\n", flush=True)
    if public:
        backends = {
            tier: {"kind": "openai", "base_url": f"{public}/v1", "model": alias}
            for alias, (tier, _) in TIERS.items()
        }
        print("\n" + "=" * 68, flush=True)
        print("PUBLIC URL:", public, flush=True)
        print("\nExport this on the cluster host, then run the preflight:\n", flush=True)
        print(f"export POLYFORGE_EVAL_TIER_BACKENDS='{json.dumps(backends)}'",
              flush=True)
        print("python eval/scripts/tunnel_preflight.py", flush=True)
        print("=" * 68 + "\n", flush=True)
        if args.announce_url:
            announce(args.announce_url, {
                "public_url": public,
                "backends": backends,
                "authenticated": auth_token is not None,
                "mock": args.mock,
                "serving_hours": args.hours,
            })

    deadline = time.time() + args.hours * 3600
    try:
        while time.time() < deadline:
            time.sleep(30)
    except KeyboardInterrupt:
        pass
    print("serving window over; shutting down", flush=True)
    server.shutdown()


if __name__ == "__main__":
    main()
