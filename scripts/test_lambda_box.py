"""lambda_box.py touches a meter; the parts that can be tested without one are.

Run: python -m pytest scripts/test_lambda_box.py -q
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "lambda_box", Path(__file__).resolve().parent / "lambda_box.py")
lb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lb)

SECRET = "secret_abc123_do_not_print"


def test_api_key_comes_from_the_file_only(tmp_path, monkeypatch):
    f = tmp_path / "api_key"
    f.write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("LAMBDA_API_KEY_FILE", str(f))
    assert lb.api_key() == SECRET
    with pytest.raises(SystemExit) as exc:
        lb.api_key(tmp_path / "absent")
    assert "cloud.lambda.ai" in str(exc.value)


def test_http_errors_carry_the_body_and_never_the_key(monkeypatch):
    def boom(req, timeout):
        assert req.get_header("Authorization") == f"Bearer {SECRET}"
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b'{"error":{"message":"bad key"}}'))
    monkeypatch.setattr(lb.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit) as exc:
        lb.request("GET", "/instances", key=SECRET)
    assert "HTTP 403" in str(exc.value) and "bad key" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_wanted_types_filters_sorts_and_reads_capacity():
    def it(cents, arch, gpu, specs):
        return {"price_cents_per_hour": cents, "architecture": arch,
                "gpu_description": gpu, "specs": specs}
    payload = {"data": {
        "gpu_1x_h100_pcie": {
            "instance_type": it(329, "x86_64", "H100 (80 GB)",
                                {"vcpus": 26, "memory_gib": 225, "gpus": 1}),
            "regions_with_capacity_available": [{"name": "us-west-1"}]},
        "gpu_1x_a100": {
            "instance_type": it(199, "x86_64", "A100 (40 GB)",
                                {"vcpus": 30, "memory_gib": 220, "gpus": 1}),
            "regions_with_capacity_available": []},
        "gpu_1x_a10": {
            "instance_type": it(129, "x86_64", "A10 (24 GB)", {}),
            "regions_with_capacity_available": [{"name": "us-east-1"}]},
        "gpu_1x_gh200": {
            "instance_type": it(229, "arm64", "GH200", {}),
            "regions_with_capacity_available": [{"name": "us-east-3"}]},
    }}
    rows = lb.wanted_types(payload)
    assert [r["name"] for r in rows] == ["gpu_1x_a100", "gpu_1x_h100_pcie"]
    assert rows[0]["regions"] == [] and rows[1]["regions"] == ["us-west-1"]
    assert rows[0]["usd_per_hour"] == 1.99


def test_launch_body_is_exactly_the_request_the_api_requires():
    body = lb.launch_body("us-west-1", "gpu_1x_a100", "polyforge-b1")
    assert body == {"region_name": "us-west-1", "instance_type_name": "gpu_1x_a100",
                    "ssh_key_names": ["polyforge-b1"], "name": "polyforge-b1"}
    json.dumps(body)


def test_wait_stops_on_a_dead_instance(monkeypatch):
    monkeypatch.setattr(lb, "request", lambda *_a, **_k: {"data": {
        "id": "x", "status": "terminated", "instance_type": {}, "region": {}}})
    with pytest.raises(SystemExit) as exc:
        lb.cmd_wait(type("A", (), {"id": "x"})())
    assert "terminated" in str(exc.value)
