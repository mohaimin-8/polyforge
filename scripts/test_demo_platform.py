"""demo_platform.py drives real processes; its decision logic is tested here.

Run: python -m pytest scripts/test_demo_platform.py -q
The end-to-end rehearsal (builds the Go services, binds local ports) runs
only with POLYFORGE_DEMO_IT=1, because CI runners share ports.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "demo_platform", Path(__file__).resolve().parent / "demo_platform.py")
dp = importlib.util.module_from_spec(spec)
sys.modules["demo_platform"] = dp  # @dataclass resolves its module here
spec.loader.exec_module(dp)


def test_tier_backends_match_the_gateway_contract():
    # internal/ai/gateway/knobs.go BuildTierProviders: tier -> {base_url, model}
    specs = json.loads(dp.tier_backends_json())
    assert set(specs) == {"small", "mid", "large"}
    for tier, spec_ in specs.items():
        assert set(spec_) == {"base_url", "model"}
        port = dict(dp.MOCK_TIERS)[tier][0]
        assert spec_["base_url"] == f"http://127.0.0.1:{port}/v1"


def test_mock_tiers_are_ordered_by_latency():
    delays = [delay for _, (_, delay) in dp.MOCK_TIERS]
    assert [t for t, _ in dp.MOCK_TIERS] == ["small", "mid", "large"]
    assert delays == sorted(delays) and len(set(delays)) == 3


def test_busy_ports_reports_a_bound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        taken = s.getsockname()[1]
        assert dp.busy_ports([taken]) == [taken]
    assert dp.busy_ports([taken]) == []


def test_latency_order_needs_strictly_increasing_tiers():
    assert dp.latency_order_ok({"small": 0.1, "mid": 0.3, "large": 0.8})
    assert not dp.latency_order_ok({"small": 0.3, "mid": 0.3, "large": 0.8})
    assert not dp.latency_order_ok({"small": 0.1, "mid": 0.8})


def test_checks_fail_the_run_when_any_check_fails(capsys):
    checks = dp.Checks()
    checks.record("one", True, "fine")
    assert checks.exit_code() == 0
    checks.record("two", False, "broken")
    assert checks.exit_code() == 1
    checks.summary()
    out = capsys.readouterr().out
    assert "1 of 2 checks failed" in out and "two" in out


@pytest.mark.skipif(os.environ.get("POLYFORGE_DEMO_IT") != "1",
                    reason="end-to-end rehearsal: set POLYFORGE_DEMO_IT=1")
def test_full_rehearsal_passes_every_check():
    assert dp.main([]) == 0


class _FakeProc:
    def __init__(self, fail_on_terminate: bool = False) -> None:
        self.fail_on_terminate = fail_on_terminate
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        if self.fail_on_terminate:
            raise OSError("handle already closed")

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        pass


class _FakeLog:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_stop_reaches_every_process_even_when_one_fails():
    services = dp.Services()
    procs = [_FakeProc(), _FakeProc(fail_on_terminate=True), _FakeProc()]
    logs = [_FakeLog() for _ in procs]
    services.procs = [(f"p{i}", p, log) for i, (p, log) in enumerate(zip(procs, logs))]
    services.stop()
    assert all(p.terminated for p in procs)
    assert all(log.closed for log in logs)
    assert services.procs == []


def test_onboarding_reply_without_a_key_is_a_clean_failure(monkeypatch):
    monkeypatch.setattr(dp, "call", lambda *a, **k: dp.Reply(201, {"tenant": {}}, {}, 0.01))
    with pytest.raises(RuntimeError, match="onboarding"):
        dp.act_onboard(dp.Checks())
