"""Unit tests of the ported congestion protocol, against a fake server."""

from __future__ import annotations

import csv
import json
import random
import threading
import time
import urllib.error

import pytest

import load_curve as lc

TINY = {"capacity_s": 0.15, "level_s": 0.05, "baseline_n": 2, "pause_s": 0.0}


class FakeServer:
    """Answers instantly with llama-server-like timings; records what it got."""

    def __init__(self, fail_every: int = 0, healthy_first: int = 0, delay: float = 0.0) -> None:
        self.lock = threading.Lock()
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []
        self.fail_every = fail_every
        self.healthy_first = healthy_first

    def __call__(self, path: str, payload: dict, timeout: float):
        with self.lock:
            self.calls.append((path, payload))
            n = len(self.calls)
        if self.fail_every and n > self.healthy_first and n % self.fail_every == 0:
            raise urllib.error.URLError("refused")
        if self.delay:
            time.sleep(self.delay)
        return 0.001, {"timings": {"prompt_n": 43, "prompt_ms": 1.0, "predicted_n": 48, "predicted_ms": 2.0}}


def args_for(tmp_path, **over):
    base = ["--tier", "small", "--replicas", "2", "--threads", "1", "--base-url", "http://fake",
            "--out", str(tmp_path / "out")]
    for k, v in over.items():
        base += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
    return lc.parse_args(base)


def test_payload_is_the_frozen_protocol():
    p = lc.protocol_payload("x")
    assert p["n_predict"] == 48 and p["ignore_eos"] is True and p["cache_prompt"] is False
    assert p["temperature"] == 0.0 and p["prompt"].endswith(lc.PROMPT_BODY)
    assert lc.RHO_LEVELS == [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90]
    assert lc.SEED == 20261112 and lc.SLOTS == 4


def test_full_run_writes_the_plant2_schema(tmp_path):
    # A 20 ms server keeps the measured capacity, and so the open-loop
    # levels' thread count, small enough for a unit test.
    server = FakeServer(delay=0.02)
    meta = lc.run(args_for(tmp_path), post=server, pause=lambda s: None, timing=TINY)
    out = tmp_path / "out"
    rows = list(csv.DictReader((out / "load_curve_requests.csv").open(encoding="utf-8")))
    assert rows and list(rows[0].keys()) == lc.FIELDS
    phases = {r["phase"] for r in rows}
    assert {"baseline", "capacity_pre", "capacity_post"} <= phases
    saved = json.loads((out / "load_curve_meta.json").read_text())
    for key in ("l0_s", "mu_pre_rps", "mu_post_rps", "levels", "level_seconds", "seed", "slots"):
        assert key in saved
    assert saved["capacity_workers"] == 2 * lc.SLOTS * 2 and meta["failed_requests"] == 0
    # warm-up: slots x replicas requests before anything is recorded
    assert all(c[0] == "/completion" for c in server.calls)


def test_capacity_only_and_trace_capacity(tmp_path):
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({"meta": {}, "requests": [{"prompt": "a"}, {"prompt": "b"}]}))
    server = FakeServer()
    meta = lc.run(args_for(tmp_path, phases="capacity", prompts=str(schedule)), post=server,
                  pause=lambda s: None, timing=TINY)
    assert "l0_s" not in meta and meta["mu_post_rps"] == meta["mu_pre_rps"]
    assert meta["mu_trace_rps"] > 0
    chats = [c for c in server.calls if c[0] == "/v1/chat/completions"]
    assert chats and {c[1]["messages"][0]["content"] for c in chats} <= {"a", "b"}
    assert all("max_tokens" not in c[1] for c in chats)


def test_open_loop_draws_poisson_arrivals_from_the_seed():
    rec = lc.Recorder()
    proto = lc.Protocol(post=FakeServer(), replicas=1, rec=rec)
    n1 = proto.open_loop(0.5, 100.0, 0.2, random.Random(1), 0)
    n2 = lc.Protocol(post=FakeServer(), replicas=1, rec=lc.Recorder()).open_loop(0.5, 100.0, 0.2, random.Random(1), 0)
    assert n1 == n2 and 3 <= n1 <= 25  # about 50 req/s x 0.2 s
    assert len([r for r in rec.rows if r["phase"] == "load"]) == n1


def test_failures_are_recorded_not_raised(tmp_path):
    # Warm-up (slots x replicas = 8 requests) must succeed: a failing warm-up
    # means the tier is not up, and the run stops there.
    meta = lc.run(args_for(tmp_path, phases="capacity"), post=FakeServer(fail_every=5, healthy_first=8),
                  pause=lambda s: None, timing=TINY)
    assert meta["failed_requests"] > 0


def test_a_failing_warm_up_stops_the_run(tmp_path):
    with pytest.raises(urllib.error.URLError):
        lc.run(args_for(tmp_path, phases="capacity"), post=FakeServer(fail_every=1),
               pause=lambda s: None, timing=TINY)


def test_capacity_with_no_completion_raises():
    def dead(path, payload, timeout):
        raise urllib.error.URLError("down")
    proto = lc.Protocol(post=dead, replicas=1, rec=lc.Recorder())
    with pytest.raises(RuntimeError, match="no request completed"):
        proto.capacity(0.05, "pre")


def test_replicas_must_be_positive(tmp_path):
    with pytest.raises(SystemExit):
        lc.parse_args(["--tier", "small", "--replicas", "0", "--base-url", "u", "--out", str(tmp_path)])
