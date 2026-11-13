"""Unit tests of tenant provisioning, against a fake control plane."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import provision as pv


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeControlPlane:
    def __init__(self, existing=(), drop_first: int = 0) -> None:
        self.tenants = dict.fromkeys(existing, "standard")
        self.drops = drop_first
        self.keys = 0
        self.seen: list[tuple[str, dict]] = []

    def __call__(self, req, timeout):
        if self.drops:
            self.drops -= 1
            raise urllib.error.URLError("connection reset")
        assert req.headers["X-polyforge-admin-key"] == "k"
        body = json.loads(req.data)
        path = req.full_url.split("30080")[1]
        self.seen.append((path, body))
        if path == "/v1/tenants":
            if body["id"] in self.tenants:
                raise urllib.error.HTTPError(req.full_url, 409, "exists", {}, io.BytesIO(b"{}"))
            self.tenants[body["id"]] = body["plan"]
            return Resp(json.dumps({"tenant": body}).encode())
        self.keys += 1
        return Resp(json.dumps({"id": f"k{self.keys}", "secret": f"secret-{self.keys}"}).encode())


BASE = "http://127.0.0.1:30080"


def test_provision_creates_tenants_with_plans_and_mints_keys():
    cp = FakeControlPlane()
    tokens = pv.provision(BASE, [{"id": "t00", "plan": "premium"}, {"id": "t01", "plan": "best-effort"}],
                          "k", opener=cp, sleep=lambda s: None)
    assert tokens == {"t00": "secret-1", "t01": "secret-2"}
    assert cp.tenants == {"t00": "premium", "t01": "best-effort"}
    assert ("/v1/tenants/t00/api-keys", {"name": "wp10", "scope": "full"}) in cp.seen


def test_existing_tenant_is_kept_and_gets_a_fresh_key():
    cp = FakeControlPlane(existing=["t00"])
    assert pv.provision(BASE, [{"id": "t00", "plan": "standard"}], "k", opener=cp, sleep=lambda s: None) == {
        "t00": "secret-1"}


def test_transport_failures_are_retried_http_errors_are_not():
    cp = FakeControlPlane(drop_first=2)
    assert pv.provision(BASE, [{"id": "t00", "plan": "standard"}], "k", opener=cp, sleep=lambda s: None)

    def forbidden(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad key"))
    with pytest.raises(pv.ProvisionError, match="401"):
        pv.post_json(BASE, "/v1/tenants", {}, "k", opener=forbidden, sleep=lambda s: None)

    def down(req, timeout):
        raise urllib.error.URLError("refused")
    with pytest.raises(pv.ProvisionError, match="5 attempts"):
        pv.post_json(BASE, "/v1/tenants", {}, "k", opener=down, sleep=lambda s: None)


def test_tenants_from_meta_and_initial_decision(tmp_path):
    meta = tmp_path / "m.json"
    meta.write_text(json.dumps({"tenants": [{"id": "t00", "slo_class": "premium"}, {"id": "t01"}]}))
    assert pv.tenants_from_meta(meta) == [{"id": "t00", "plan": "premium"}, {"id": "t01", "plan": "standard"}]
    meta.write_text(json.dumps({"tenants": []}))
    with pytest.raises(pv.ProvisionError):
        pv.tenants_from_meta(meta)
    d = pv.initial_decision(["t00"], "small", 128)
    assert d["tenants"] == {"t00": {"model_tier": "small", "cache_size_mb": 128}} and d["t_s"] == 0


def test_main_writes_tokens_and_never_prints_them(tmp_path, monkeypatch, capsys):
    meta = tmp_path / "m.json"
    meta.write_text(json.dumps({"tenants": [{"id": "t00", "slo_class": "standard"}]}))
    key = tmp_path / "admin-key.txt"
    key.write_text("k")
    monkeypatch.setattr(pv, "provision", lambda base, tenants, admin_key: {"t00": "very-secret"})
    assert pv.main(["--meta", str(meta), "--admin-key-file", str(key), "--out-dir", str(tmp_path / "o")]) == 0
    assert json.loads((tmp_path / "o" / "tokens.json").read_text()) == {"t00": "very-secret"}
    assert "very-secret" not in capsys.readouterr().out
    assert json.loads((tmp_path / "o" / "initial_decision.json").read_text())["tenants"]["t00"]["cache_size_mb"] == 128
