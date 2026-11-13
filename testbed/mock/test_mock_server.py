"""Unit tests of the mode A mock tier. The HTTP handler runs over an in-memory
connection: no socket is opened."""

from __future__ import annotations

import hashlib
import io
import json
import math
import random
import statistics

import pytest

import mock_server as ms


class FakeConnection:
    """Just enough of a socket for StreamRequestHandler: bytes in, bytes out."""

    def __init__(self, request: bytes) -> None:
        self.request = io.BytesIO(request)
        self.sent = bytearray()

    def makefile(self, mode: str, bufsize: int = -1):
        return self.request if "r" in mode else io.BytesIO()

    def sendall(self, data: bytes) -> None:
        self.sent += data


def make_state(**over) -> ms.MockState:
    base = dict(sampler=ms.LatencySampler.from_spec({"kind": "samples", "values_s": [0.25]}),
                store=ms.AnswerStore.from_spec({"answers": ["alpha", "beta", "gamma"]}),
                model="tier-small", metrics=ms.Metrics(), rng=random.Random(7), sleep=lambda s: None)
    base.update(over)
    return ms.MockState(**base)


def http(state: ms.MockState, method: str, path: str, body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, dict, bytes]:
    lines = [f"{method} {path} HTTP/1.1", "Host: mock"]
    hdrs = dict(headers or {})
    if body is not None and "Content-Length" not in hdrs:
        hdrs["Content-Length"] = str(len(body))
    lines += [f"{k}: {v}" for k, v in hdrs.items()]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode() + (body or b"")
    conn = FakeConnection(raw)
    ms.make_handler(state)(conn, ("127.0.0.1", 50000), object())
    head, _, payload = bytes(conn.sent).partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode().split("\r\n")
    parsed = dict(line.split(": ", 1) for line in header_lines)
    return int(status_line.split()[1]), parsed, payload


def chat_body(prompt: str, stream: bool = False) -> bytes:
    return json.dumps({"model": "tier-small", "stream": stream,
                       "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": prompt}]}).encode()


def test_samples_resample_recorded_values():
    sampler = ms.LatencySampler.from_spec({"kind": "samples", "values_s": [0.5, 1.5]}, scale=2.0)
    rng = random.Random(1)
    assert {sampler.sample(rng) for _ in range(50)} == {1.0, 3.0}


def test_quantiles_interpolate_the_inverse_cdf():
    sampler = ms.LatencySampler.from_spec({"kind": "quantiles", "p": [0, 0.5, 1], "values_s": [1, 2, 6]})
    rng = random.Random(3)
    draws = [sampler.sample(rng) for _ in range(4000)]
    assert min(draws) >= 1 and max(draws) <= 6
    assert statistics.median(draws) == pytest.approx(2.0, abs=0.1)


def test_lognormal_matches_its_parameters():
    sampler = ms.LatencySampler.from_spec({"kind": "lognormal", "mu": math.log(2.0), "sigma": 0.1})
    rng = random.Random(5)
    draws = [sampler.sample(rng) for _ in range(4000)]
    assert statistics.median(draws) == pytest.approx(2.0, rel=0.02)


@pytest.mark.parametrize("spec", [
    {"kind": "samples", "values_s": []},
    {"kind": "samples", "values_s": [-1]},
    {"kind": "quantiles", "p": [0.1, 1], "values_s": [1, 2]},
    {"kind": "quantiles", "p": [0, 1], "values_s": [2, 1]},
    {"kind": "lognormal", "mu": 0, "sigma": -1},
    {"kind": "gamma"},
])
def test_bad_latency_specs_are_refused(spec):
    with pytest.raises(ms.SpecError):
        ms.LatencySampler.from_spec(spec)


def test_answer_store_is_deterministic_and_prefers_exact_hashes():
    prompt = "what is a semantic cache?"
    exact = {hashlib.sha256(prompt.encode()).hexdigest(): "exact answer"}
    store = ms.AnswerStore.from_spec({"answers": ["a", "b"], "by_prompt_sha256": exact})
    assert store.pick(prompt) == "exact answer"
    other = ms.AnswerStore.from_spec({"answers": ["a", "b", "c"]})
    assert other.pick("x") == other.pick("x") and other.pick("x") in {"a", "b", "c"}
    with pytest.raises(ms.SpecError):
        ms.AnswerStore.from_spec({})


def test_last_user_message_and_its_errors():
    assert ms.last_user_message([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                                 {"role": "user", "content": "c"}]) == "c"
    for bad in ([], None, [{"role": "system", "content": "s"}], ["x"], [{"role": "user", "content": 3}]):
        with pytest.raises(ValueError):
            ms.last_user_message(bad)


def test_chat_returns_a_stored_answer_after_the_drawn_latency():
    slept = []
    state = make_state(sleep=slept.append)
    code, headers, payload = http(state, "POST", "/v1/chat/completions", chat_body("hello"))
    assert code == 200 and headers["Content-Type"] == "application/json"
    body = json.loads(payload)
    assert body["choices"][0]["message"]["content"] == state.store.pick("hello")
    assert body["model"] == "tier-small" and body["usage"]["completion_tokens"] >= 1
    assert slept == [0.25]


def test_stream_mode_emits_sse_with_done():
    state = make_state()
    code, headers, payload = http(state, "POST", "/v1/chat/completions", chat_body("hi", stream=True))
    assert code == 200 and headers["Content-Type"] == "text/event-stream"
    events = [e for e in payload.decode().split("\n\n") if e]
    assert events[-1] == "data: [DONE]"
    first = json.loads(events[0][len("data: "):])
    assert first["choices"][0]["delta"]["content"] == state.store.pick("hi")


@pytest.mark.parametrize("body, headers, code", [
    (b"not json", None, 400),
    (json.dumps({"messages": []}).encode(), None, 400),
    (json.dumps(["list"]).encode(), None, 400),
    (b"", {"Content-Length": "0"}, 413),
    (b"x", {"Content-Length": "abc"}, 411),
])
def test_malformed_requests_are_refused(body, headers, code):
    state = make_state()
    got, hdrs, _ = http(state, "POST", "/v1/chat/completions", body, headers)
    assert got == code and hdrs.get("Connection") == "close"


def test_health_metrics_and_unknown_paths():
    state = make_state()
    assert http(state, "GET", "/health")[0] == 200
    http(state, "POST", "/v1/chat/completions", chat_body("a"))
    code, _, metrics = http(state, "GET", "/metrics")
    text = metrics.decode()
    assert code == 200
    assert 'mock_requests_total{code="200"} 1' in text
    assert 'mock_injected_latency_seconds_bucket{le="0.1"} 0' in text
    assert 'mock_injected_latency_seconds_bucket{le="0.25"} 1' in text
    assert 'mock_injected_latency_seconds_bucket{le="+Inf"} 1' in text
    assert "mock_inflight_requests 0" in text
    assert http(state, "GET", "/nope")[0] == 404
    assert http(state, "POST", "/v1/embeddings", b"{}")[0] == 404


def test_seeded_state_draws_a_reproducible_sequence():
    spec = {"kind": "lognormal", "mu": 0.0, "sigma": 0.5}
    a = make_state(sampler=ms.LatencySampler.from_spec(spec), rng=random.Random(11))
    b = make_state(sampler=ms.LatencySampler.from_spec(spec), rng=random.Random(11))
    assert [a.next_request() for _ in range(5)] == [b.next_request() for _ in range(5)]


def test_shipped_data_files_load():
    for name in ("latency_small.json", "latency_mid.json"):
        sampler = ms.LatencySampler.from_spec(ms.load_json(str(ms.Path(__file__).parent / "data" / name)))
        assert sampler.kind == "lognormal" and 0.5 < math.exp(sampler.mu) < 5
    store = ms.AnswerStore.from_spec(ms.load_json(str(ms.Path(__file__).parent / "data" / "answers.json")))
    assert len(store.answers) == 64


def test_main_refuses_a_missing_spec_file(tmp_path):
    assert ms.main(["--latency", str(tmp_path / "missing.json"), "--answers", str(tmp_path / "a.json")]) == 2
