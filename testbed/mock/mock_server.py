"""Mode A tier endpoint: an OpenAI-compatible chat server that answers each
request after a latency drawn from a recorded distribution, with a stored
answer (programme 2, WP10).

Mode A models an API-backed tier: the endpoint never congests, so the delay is
independent of load and every request runs on its own thread. The answer is
picked deterministically from a store by the prompt's sha256, so a repeated
prompt gets the same completion (what the gateway's cache would hold) and the
completion sizes follow the store's recorded distribution.

Standard library only; it runs in the pinned python slim image with this file
and its data mounted from ConfigMaps.

    python mock_server.py --latency data/latency_small.json --answers data/answers.json \
        --model tier-small --port 8080
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

LOG = logging.getLogger("mock")
MAX_BODY_BYTES = 1 << 20
LATENCY_BUCKETS_S = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
CHARS_PER_TOKEN = 4  # usage counts are an estimate; nothing in the gateway prices them


class SpecError(ValueError):
    """A latency or answer file that cannot be used."""


@dataclass(frozen=True)
class LatencySampler:
    """Draws one service latency in seconds from a recorded distribution.

    kinds: ``samples`` (resample the recorded values), ``quantiles`` (inverse
    CDF, linear between the recorded points), ``lognormal`` (mu, sigma of log
    seconds, e.g. fitted to a recorded mean and p95).
    """

    kind: str
    values: tuple[float, ...] = ()
    probs: tuple[float, ...] = ()
    mu: float = 0.0
    sigma: float = 0.0
    scale: float = 1.0

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any], scale: float = 1.0) -> "LatencySampler":
        if scale <= 0:
            raise SpecError("time scale must be positive")
        kind = spec.get("kind")
        if kind == "samples":
            values = tuple(float(v) for v in spec.get("values_s", ()))
            if not values or min(values) < 0:
                raise SpecError("samples need at least one non-negative value in values_s")
            return cls(kind=kind, values=values, scale=scale)
        if kind == "quantiles":
            probs = tuple(float(p) for p in spec.get("p", ()))
            values = tuple(float(v) for v in spec.get("values_s", ()))
            if len(probs) < 2 or len(probs) != len(values):
                raise SpecError("quantiles need matching p and values_s with at least two points")
            if probs[0] != 0.0 or probs[-1] != 1.0 or list(probs) != sorted(set(probs)):
                raise SpecError("quantile probabilities must rise strictly from 0 to 1")
            if list(values) != sorted(values) or values[0] < 0:
                raise SpecError("quantile values must be non-negative and non-decreasing")
            return cls(kind=kind, values=values, probs=probs, scale=scale)
        if kind == "lognormal":
            sigma = float(spec.get("sigma", -1))
            if sigma < 0 or not math.isfinite(float(spec.get("mu", math.nan))):
                raise SpecError("lognormal needs a finite mu and sigma >= 0")
            return cls(kind=kind, mu=float(spec["mu"]), sigma=sigma, scale=scale)
        raise SpecError(f"unknown latency kind {kind!r}: use samples, quantiles or lognormal")

    def sample(self, rng: random.Random) -> float:
        if self.kind == "samples":
            return self.scale * rng.choice(self.values)
        if self.kind == "quantiles":
            u = rng.random()
            i = min(max(bisect.bisect_right(self.probs, u), 1), len(self.probs) - 1)
            p0, p1 = self.probs[i - 1], self.probs[i]
            v0, v1 = self.values[i - 1], self.values[i]
            return self.scale * (v0 + (v1 - v0) * (u - p0) / (p1 - p0))
        return self.scale * rng.lognormvariate(self.mu, self.sigma)


@dataclass(frozen=True)
class AnswerStore:
    """Stored completions: an exact answer for a known prompt hash, else one of
    the stored answers chosen by the prompt's hash (stable across restarts)."""

    answers: tuple[str, ...]
    by_prompt_sha256: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> "AnswerStore":
        answers = tuple(str(a) for a in spec.get("answers", ()))
        by_sha = {str(k): str(v) for k, v in (spec.get("by_prompt_sha256") or {}).items()}
        if not answers and not by_sha:
            raise SpecError("an answer store needs answers or by_prompt_sha256 entries")
        if not answers:
            answers = tuple(sorted(by_sha.values()))
        return cls(answers=answers, by_prompt_sha256=by_sha)

    def pick(self, prompt: str) -> str:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        exact = self.by_prompt_sha256.get(digest)
        if exact is not None:
            return exact
        return self.answers[int(digest[:12], 16) % len(self.answers)]


class Metrics:
    """Prometheus text-format counters for the mock, thread-safe."""

    def __init__(self, buckets: Sequence[float] = LATENCY_BUCKETS_S) -> None:
        self._lock = threading.Lock()
        self._buckets = tuple(buckets)
        self._bucket_counts = [0] * len(self._buckets)
        self._latency_sum = 0.0
        self._latency_count = 0
        self._codes: dict[int, int] = {}
        self._inflight = 0
        self._answer_bytes = 0

    def start(self) -> None:
        with self._lock:
            self._inflight += 1

    def finish(self, code: int, injected_s: float | None, answer_bytes: int) -> None:
        with self._lock:
            self._inflight -= 1
            self._codes[code] = self._codes.get(code, 0) + 1
            self._answer_bytes += answer_bytes
            if injected_s is not None:
                self._latency_sum += injected_s
                self._latency_count += 1
                for i, bound in enumerate(self._buckets):
                    if injected_s <= bound:
                        self._bucket_counts[i] += 1

    def render(self) -> str:
        with self._lock:
            lines = ["# TYPE mock_requests_total counter"]
            lines += [f'mock_requests_total{{code="{c}"}} {n}' for c, n in sorted(self._codes.items())]
            lines += ["# TYPE mock_inflight_requests gauge", f"mock_inflight_requests {self._inflight}",
                      "# TYPE mock_answer_bytes_total counter", f"mock_answer_bytes_total {self._answer_bytes}",
                      "# TYPE mock_injected_latency_seconds histogram"]
            lines += [f'mock_injected_latency_seconds_bucket{{le="{b:g}"}} {n}'
                      for b, n in zip(self._buckets, self._bucket_counts)]
            lines += [f'mock_injected_latency_seconds_bucket{{le="+Inf"}} {self._latency_count}',
                      f"mock_injected_latency_seconds_sum {self._latency_sum:.6f}",
                      f"mock_injected_latency_seconds_count {self._latency_count}"]
        return "\n".join(lines) + "\n"


def last_user_message(messages: Any) -> str:
    """Content of the last user message; raises ValueError on a malformed list."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            raise ValueError("each message must be an object")
        if msg.get("role") == "user":
            content = msg.get("content")
            if not isinstance(content, str):
                raise ValueError("user message content must be a string")
            return content
    raise ValueError("messages contain no user message")


def chat_completion(model: str, answer: str, prompt: str, request_id: str, created: int) -> dict:
    prompt_tokens = max(1, math.ceil(len(prompt) / CHARS_PER_TOKEN))
    completion_tokens = max(1, math.ceil(len(answer) / CHARS_PER_TOKEN))
    return {
        "id": request_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


def stream_events(model: str, answer: str, request_id: str, created: int) -> list[bytes]:
    """The completion as server-sent events: one content delta, then the end."""
    def event(delta: dict, finish: str | None) -> bytes:
        chunk = {"id": request_id, "object": "chat.completion.chunk", "created": created,
                 "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
    return [event({"role": "assistant", "content": answer}, None), event({}, "stop"), b"data: [DONE]\n\n"]


@dataclass
class MockState:
    sampler: LatencySampler
    store: AnswerStore
    model: str
    metrics: Metrics
    rng: random.Random
    sleep: Callable[[float], None] = time.sleep
    rng_lock: threading.Lock = field(default_factory=threading.Lock)
    counter: int = 0

    def next_request(self) -> tuple[str, float]:
        """A request id and the latency to inject, drawn under one lock so a
        seeded run draws the same sequence regardless of thread scheduling."""
        with self.rng_lock:
            self.counter += 1
            return f"mock-{self.counter}", self.sampler.sample(self.rng)


def make_handler(state: MockState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "polyforge-wp10-mock"

        def log_message(self, fmt: str, *args: Any) -> None:  # per-request stderr lines off
            LOG.debug(fmt, *args)

        def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, code: int, message: str) -> None:
            # The request body may be unread: never reuse this connection.
            self.close_connection = True
            body = json.dumps({"error": {"message": message, "type": "invalid_request_error"}})
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path == "/health":
                self._send(200, b'{"status":"ok"}')
            elif self.path == "/metrics":
                self._send(200, state.metrics.render().encode("utf-8"), "text/plain; version=0.0.4")
            else:
                self._error(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/v1/chat/completions", "/chat/completions"):
                self._error(404, "not found")
                return
            state.metrics.start()
            code, injected, answer_bytes = 500, None, 0
            try:
                code, injected, answer_bytes = self._chat()
            finally:
                state.metrics.finish(code, injected, answer_bytes)

        def _chat(self) -> tuple[int, float | None, int]:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._error(411, "Content-Length is required")
                return 411, None, 0
            if length <= 0 or length > MAX_BODY_BYTES:
                self._error(413, f"body must be 1..{MAX_BODY_BYTES} bytes")
                return 413, None, 0
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                prompt = last_user_message(body.get("messages"))
            except (ValueError, UnicodeDecodeError) as err:
                self._error(400, str(err))
                return 400, None, 0
            request_id, delay = state.next_request()
            state.sleep(delay)
            answer = state.store.pick(prompt)
            created = int(time.time())
            if body.get("stream") is True:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk in stream_events(state.model, answer, request_id, created):
                    self.wfile.write(chunk)
                self.close_connection = True
            else:
                payload = chat_completion(state.model, answer, prompt, request_id, created)
                self._send(200, json.dumps(payload).encode("utf-8"))
            return 200, delay, len(answer.encode("utf-8"))

    return Handler


class MockHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256  # bursts queue in the kernel, not as refused connections


def load_json(path: str) -> Mapping[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SpecError(f"cannot read {path}: {err}") from err


def build_state(args: argparse.Namespace) -> MockState:
    sampler = LatencySampler.from_spec(load_json(args.latency), scale=args.time_scale)
    store = AnswerStore.from_spec(load_json(args.answers))
    return MockState(sampler=sampler, store=store, model=args.model, metrics=Metrics(),
                     rng=random.Random(args.seed))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--latency", required=True, help="latency distribution JSON")
    ap.add_argument("--answers", required=True, help="answer store JSON")
    ap.add_argument("--model", default="mock", help="model name echoed in responses")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--seed", type=int, default=20261113, help="latency draw seed")
    ap.add_argument("--time-scale", type=float, default=1.0, help="multiply every drawn latency")
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        state = build_state(args)
    except SpecError as err:
        LOG.error("%s", err)
        return 2
    server = MockHTTPServer((args.host, args.port), make_handler(state))
    LOG.info("mock tier %s listening on %s:%d (latency %s, %d stored answers)",
             args.model, args.host, args.port, state.sampler.kind, len(state.store.answers))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
