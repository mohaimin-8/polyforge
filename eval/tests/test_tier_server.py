"""Tests for the Kaggle tier server's public-exposure guards.

A cloudflared quick tunnel puts this server on the open internet behind
nothing but a random subdomain, with someone's GPU behind it. Two properties
therefore have to hold, and neither is exercised by the happy path: the bearer
token must actually gate /v1/*, and the announce step -- which exists because
a Kaggle kernel's log is unreadable while it runs -- must publish the URL
without taking the server down when the rendezvous is unreachable.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "calibration"))

import kaggle_tier_server as kts  # noqa: E402


@pytest.fixture
def serve():
    servers = []

    def start(token=None):
        models = kts.TierModels(mock=True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), kts.make_handler(models, token))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()


def post(base, token=None, model="qwen2.5-0.5b-instruct"):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps({"model": model,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def test_no_token_configured_stays_open(serve):
    assert post(serve(None)) == 200


def test_token_configured_rejects_missing_and_wrong(serve):
    base = serve("s3cret")
    assert post(base, None) == 401
    assert post(base, "wrong") == 401


def test_token_configured_accepts_correct(serve):
    base = serve("s3cret")
    assert post(base, "s3cret") == 200


def test_healthz_stays_open_for_tunnel_probes(serve):
    base = serve("s3cret")
    with urllib.request.urlopen(base + "/healthz", timeout=30) as resp:
        assert resp.status == 200


def test_announce_publishes_the_payload():
    seen = {}

    class Sink(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            seen.update(json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/rendezvous"
        assert kts.announce(url, {"public_url": "https://x.trycloudflare.com"}) is True
        assert seen["public_url"] == "https://x.trycloudflare.com"
    finally:
        srv.shutdown()


def test_announce_failure_is_not_fatal():
    """The kernel must keep serving even if the rendezvous is down -- the URL
    is still in the log for a human, which is the manual fallback."""
    assert kts.announce("http://127.0.0.1:9/nope", {"public_url": "x"}, attempts=1) is False


# --- Micro-batching (docs/ZERO_COST_ROADMAP.md Phase 2) --------------------
#
# The per-tier lock served 0.5 req/s and the frozen `wave4_live_plane` cell
# needs ~110 AI rps at burst peak, which is how the 2026-08-31 matrix died at
# 48.3% failed requests. Batching is what closes that, and it is only safe
# because `min_new_tokens == max_new_tokens == 48` with `do_sample=False`
# makes every request decode exactly 48 greedy steps.
#
# `test_concurrent_requests_share_one_batch` is the test that would have caught
# the original problem, and `test_left_padding_is_used` is the one that catches
# the way batching goes silently wrong.

# A ~2 MB random-weight Llama. Real HF `generate` semantics, no GPU, and
# cached after the first run -- so the one check that can catch a silent
# padding bug runs by DEFAULT rather than waiting for a Kaggle session.
# Override with POLYFORGE_TIER_TEST_MODEL to point at a bigger model.
DEFAULT_TEST_MODEL = "hf-internal-testing/tiny-random-LlamaForCausalLM"
REAL_MODEL = os.environ.get("POLYFORGE_TIER_TEST_MODEL", DEFAULT_TEST_MODEL)


class _StubTokenizer:
    """Just enough HF tokenizer surface for `pad_batch`, and it records the
    `padding_side` that was in force when it was called."""

    def __init__(self):
        self.pad_token = None
        self.eos_token = "<eos>"
        self.padding_side = "right"
        self.seen_padding_side = None
        self.seen_texts = None

    def __call__(self, texts, return_tensors=None, padding=None):
        self.seen_padding_side = self.padding_side
        self.seen_texts = list(texts)
        width = max(len(t) for t in texts)
        return {
            "input_ids": [[0] * (width - len(t)) + [ord(c) for c in t]
                          for t in texts],
            "attention_mask": [[0] * (width - len(t)) + [1] * len(t)
                               for t in texts],
        }


def test_left_padding_is_used_and_restored():
    """Decoder-only generation continues from the LAST position. Right padding
    makes a short row continue from PAD and return fluent nonsense -- wrong
    output, not an error -- so the padding side is the whole ballgame."""
    tok = _StubTokenizer()
    kts.pad_batch(tok, ["aaa", "b"])
    assert tok.seen_padding_side == "left"
    assert tok.padding_side == "right", "the caller's setting must be restored"
    assert tok.pad_token == "<eos>", "a missing pad token must be filled in"


def test_left_padding_mask_marks_the_real_prompt_not_the_padding():
    """`prompt_tokens` is read off the attention mask, not the padded width.
    Reading the width would over-report every short prompt in the batch."""
    tok = _StubTokenizer()
    out = kts.pad_batch(tok, ["aaaa", "b"])
    assert out["attention_mask"][0] == [1, 1, 1, 1]
    assert out["attention_mask"][1] == [0, 0, 0, 1], "short row pads on the LEFT"
    assert sum(out["attention_mask"][1]) == 1


def test_concurrent_requests_share_one_batch(serve):
    """The point of the change, stated as a timing assertion.

    Mock `mid` sleeps 1.88 s per BATCH. Serialised, 12 requests take ~22.6 s;
    batched they take one sleep plus the window. A regression back to per-
    request serialisation fails here loudly rather than only showing up as a
    48.3% failure rate on a GPU we are not running in CI.
    """
    base = serve()
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda _: post(base, model="qwen2.5-3b-instruct"),
                              range(12)))
    elapsed = time.time() - started
    assert codes == [200] * 12
    serial = 12 * kts.MOCK_DELAY_S["mid"]
    assert elapsed < serial / 3, (
        f"12 concurrent requests took {elapsed:.1f}s; serialised would be "
        f"~{serial:.1f}s, so they were not batched")


def test_batch_window_flushes_on_timeout_not_only_on_fullness(serve):
    """A batch that never fills must still decode. With `batch_max` at 64 and
    one request in flight, only the window can release it."""
    base = serve()
    started = time.time()
    assert post(base) == 200
    elapsed = time.time() - started
    assert elapsed < kts.MOCK_DELAY_S["small"] + 2.0, (
        "a lone request waited far longer than one window + one decode")


def test_a_failed_batch_wakes_every_waiter(serve):
    """If the decode raises, every job in the batch must be released with the
    error. A batch that swallows an exception leaves its callers blocked
    forever, which on the live plane reads as a hung tunnel."""
    models = kts.TierModels(mock=True)
    try:
        models._generate_batch = lambda tier, batch: (_ for _ in ()).throw(
            RuntimeError("decode exploded"))
        with pytest.raises(RuntimeError, match="decode exploded"):
            models.generate("small", [{"role": "user", "content": "hi"}])
    finally:
        models.close()


def test_batched_generation_matches_serial_exactly():
    """The assertion the whole batching change rests on.

    Greedy decoding at a fixed length is deterministic, so a batched decode
    MUST be token-identical to a serial one. If padding or the mask is wrong
    this fails; nothing else in the suite would notice, because the wrong
    output is still well-formed text.

    Run in the Kaggle smoke before TIER_BENCH_BATCHED.md is written.
    """
    # torch and transformers are NOT harness dependencies - this check runs in
    # the Kaggle smoke where they exist. In CI they do not, and importing them
    # unguarded turned "this environment cannot run the check" into a failed
    # test. The load failure below was already treated as a skip; the import
    # has to be as well.
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    AutoModelForCausalLM = transformers.AutoModelForCausalLM
    AutoTokenizer = transformers.AutoTokenizer

    try:
        tok = AutoTokenizer.from_pretrained(REAL_MODEL)
        model = AutoModelForCausalLM.from_pretrained(REAL_MODEL)
    except Exception as err:  # noqa: BLE001 -- offline is a skip, not a failure
        pytest.skip(f"cannot load {REAL_MODEL}: {type(err).__name__}: {err}")
    model.eval()
    # Deliberately ragged: equal-length prompts would pad to nothing and the
    # test would pass without exercising the thing it exists for.
    texts = ["The capital of France is",
             "Once upon a time in a distant land there lived",
             "2 + 2 ="]

    serial = []
    for text in texts:
        one = tok([text], return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**one, max_new_tokens=8, min_new_tokens=8,
                                 do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        serial.append(out[0][one["input_ids"].shape[1]:].tolist())

    batch = kts.pad_batch(tok, texts)
    with torch.no_grad():
        out = model.generate(**batch, max_new_tokens=8, min_new_tokens=8,
                             do_sample=False,
                             pad_token_id=tok.pad_token_id or tok.eos_token_id)
    width = batch["input_ids"].shape[1]
    batched = [out[i][width:].tolist() for i in range(len(texts))]

    assert batched == serial, "batched decode diverged from serial decode"
