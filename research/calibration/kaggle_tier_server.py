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
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_NEW_TOKENS = 48
TIERS = {
    "qwen2.5-0.5b-instruct": ("small", "Qwen/Qwen2.5-0.5B-Instruct"),
    "qwen2.5-3b-instruct": ("mid", "Qwen/Qwen2.5-3B-Instruct"),
}
# Mock delays approximate TIER_BENCH.md's measured means, so --mock exercises
# the same *shape* the WL-H2 tier probe looks for (a material small/mid gap)
# without a GPU. They are not measurements and never enter a record.
MOCK_DELAY_S = {"small": 1.24, "mid": 1.88}


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


class TierModels:
    """Both tiers resident. HF `generate` is not thread-safe, so each tier
    holds a lock: concurrent gateway requests queue per tier rather than
    corrupting a decode. That serializes per tier, which is the honest
    behaviour of a single card — and the sim's own tier model is
    single-stream (TIER_BENCH.md measured single-stream latency)."""

    def __init__(self, mock: bool = False):
        self.mock = mock
        self.models: dict[str, tuple] = {}
        self.locks = {tier: threading.Lock() for tier, _ in TIERS.values()}
        if mock:
            print("mock mode: no models loaded", flush=True)
            return
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

    def generate(self, tier: str, messages: list[dict]) -> tuple[str, int, int]:
        if self.mock:
            time.sleep(MOCK_DELAY_S.get(tier, 1.0))
            return f"[mock {tier} reply]", 16, MAX_NEW_TOKENS
        import torch

        tok, model = self.models[tier]
        text = tok.apply_chat_template(messages, tokenize=False,
                                       add_generation_prompt=True)
        with self.locks[tier]:
            inputs = tok([text], return_tensors="pt").to(model.device)
            with torch.no_grad():
                out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS,
                                     do_sample=False,
                                     pad_token_id=tok.eos_token_id)
            prompt_tokens = int(inputs.input_ids.shape[1])
            new_tokens = out[0][prompt_tokens:]
            reply = tok.decode(new_tokens, skip_special_tokens=True)
        return reply, prompt_tokens, int(new_tokens.shape[0])


def make_handler(models: TierModels):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9109)
    ap.add_argument("--mock", action="store_true",
                    help="skip model loading; canned replies with tier-shaped "
                         "delays, for checking the HTTP contract off-GPU")
    ap.add_argument("--no-tunnel", action="store_true")
    ap.add_argument("--hours", type=float, default=8.0,
                    help="serve for this long, then exit cleanly (Kaggle "
                         "kernels are time-boxed; leaving early frees quota)")
    args = ap.parse_args()

    models = TierModels(mock=args.mock)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(models))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"tier server listening on :{args.port}", flush=True)

    public = None if args.no_tunnel else start_tunnel(args.port)
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
