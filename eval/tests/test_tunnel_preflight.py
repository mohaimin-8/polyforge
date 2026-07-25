"""Tests for the B1 free-route quota guard (eval/scripts/tunnel_preflight.py).

The probe exists to decide, before any GPU hour is spent, whether tunnelled
tier backends can still satisfy WL-H2's materiality rule. Its verdict is only
worth trusting if it (a) fires on a genuinely blurred pair and (b) does not
fire on a separable one, so both directions are pinned here against a stub
server rather than a real tunnel.

The stub also lets the "how much more round-trip would this tolerate?"
arithmetic be checked against a known answer, which is the number the runbook
tells a user to read before committing to the route.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tunnel_preflight as tp  # noqa: E402


def make_stub(delays_ms: dict[str, float]):
    """An OpenAI-compatible stub whose per-model latency we choose."""
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            model = payload.get("model", "")
            import time
            time.sleep(delays_ms.get(model, 0.0) / 1000.0)
            body = json.dumps({
                "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture
def stub():
    servers = []

    def start(delays_ms: dict[str, float]):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_stub(delays_ms))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/v1"

    yield start
    for server in servers:
        server.shutdown()


def backends(base: str) -> dict:
    return {"small": {"kind": "openai", "base_url": base, "model": "small-model"},
            "mid": {"kind": "openai", "base_url": base, "model": "mid-model"}}


def run(monkeypatch, tmp_path, base, probes=3, extra=()):
    report = tmp_path / "tunnel_preflight.json"
    argv = ["tunnel_preflight.py", "--backends", json.dumps(backends(base)),
            "--probes", str(probes), "--report", str(report), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    code = tp.main()
    return code, json.loads(report.read_text(encoding="utf-8"))


def test_separable_tiers_pass(monkeypatch, tmp_path, stub):
    # 120 vs 400 ms: gap 280 >= max(20, 0.25*120) = 30.
    base = stub({"small-model": 120.0, "mid-model": 400.0})
    code, report = run(monkeypatch, tmp_path, base)
    assert code == 0
    assert report["latency_moved"] and report["models_echoed"]
    assert report["verdict"].startswith("TUNNEL OK")


def test_blurred_tiers_fail_and_do_not_greenlight_a_run(monkeypatch, tmp_path, stub):
    # Both tiers at the same latency is what an over-slow path looks like:
    # the knob is present but not measurable, which is exactly the inert-knob
    # condition WL-H2 voids a run for.
    base = stub({"small-model": 200.0, "mid-model": 205.0})
    code, report = run(monkeypatch, tmp_path, base)
    assert code == 1
    assert not report["latency_moved"]
    assert "INADEQUATE" in report["verdict"]


def test_wrong_model_echo_is_caught(monkeypatch, tmp_path, stub):
    """A backend that ignores the model field would make both tiers the same
    server while still looking fast -- the tier knob would be inert in the
    worst way, silently. Latency alone cannot see it; the echo check can."""
    class Ignoring(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps({
                "model": "whatever-i-feel-like",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Ignoring)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/v1"
        code, report = run(monkeypatch, tmp_path, base)
        assert code == 1
        assert not report["models_echoed"]
    finally:
        server.shutdown()


def test_rtt_headroom_matches_the_materiality_rule(monkeypatch, tmp_path, stub):
    """The runbook tells the user to read `extra_rtt_tolerance_ms` before
    committing to the free route, so the arithmetic behind it is pinned:
    raising both means by r raises the threshold by rel_margin*r, so the
    slack is (delta - threshold)/rel_margin."""
    base = stub({"small-model": 120.0, "mid-model": 400.0})
    _, report = run(monkeypatch, tmp_path, base)
    expected = (report["delta_ms"] - report["threshold_ms"]) / tp.REL_MARGIN
    assert report["extra_rtt_tolerance_ms"] == pytest.approx(expected)
    # Sanity: a real tunnel adds tens to low hundreds of ms, so a separable
    # pair should show far more headroom than that.
    assert report["extra_rtt_tolerance_ms"] > 500.0


def test_margins_track_the_real_gate():
    """This probe is only useful if it predicts knob_preflight's verdict, so
    its thresholds must stay equal to that script's defaults. If the gate's
    defaults change, this fails and points at the divergence."""
    gate = Path(__file__).resolve().parents[1] / "scripts" / "knob_preflight.py"
    text = gate.read_text(encoding="utf-8")
    assert f'default={tp.REL_MARGIN}' in text, "tier-rel-margin drifted from the gate"
    assert f'default={tp.ABS_MARGIN_MS}' in text, "tier-abs-margin drifted from the gate"


def test_unreachable_backend_reports_rather_than_crashes(monkeypatch, tmp_path):
    code, report = run(monkeypatch, tmp_path, "http://127.0.0.1:9/v1")
    assert code == 1
    assert report["verdict"].startswith("UNREACHABLE")


def test_malformed_backends_json_is_rejected(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["tunnel_preflight.py", "--backends", "{nope"])
    assert tp.main() == 1
    assert "malformed" in capsys.readouterr().err


def test_slo_headroom_is_reported_and_warns_when_over_target(monkeypatch, tmp_path, stub, capsys):
    """The binding constraint on the free route is usually the premium AI SLO
    (2500 ms), not WL-H2's tier gap -- a path can keep the tiers separable
    while pushing both over target, which saturates the SLO term the arms are
    compared on. The probe has to say so rather than just report PASS."""
    # Separable (gap 1000 ms) but the slow tier is past the premium target.
    base = stub({"small-model": 1800.0, "mid-model": 2800.0})
    code, report = run(monkeypatch, tmp_path, base)
    assert code == 0, "tier gap is intact, so WL-H2's rule still passes"
    assert report["slowest_tier_within_premium_slo"] is False
    assert report["slo_headroom_ms"] < 0
    assert "violates the premium AI SLO" in capsys.readouterr().err


def test_slo_headroom_positive_when_within_target(monkeypatch, tmp_path, stub):
    base = stub({"small-model": 120.0, "mid-model": 400.0})
    _, report = run(monkeypatch, tmp_path, base)
    assert report["slowest_tier_within_premium_slo"] is True
    assert report["slo_headroom_ms"] == pytest.approx(
        tp.AI_SLO_PREMIUM_MS - report["slowest_tier_mean_ms"], abs=1.0)
