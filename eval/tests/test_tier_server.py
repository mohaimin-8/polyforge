"""Tests for the Kaggle tier server's public-exposure guards.

A cloudflared quick tunnel puts this server on the open internet behind
nothing but a random subdomain, with someone's GPU behind it. Two properties
therefore have to hold, and neither is exercised by the happy path: the bearer
token must actually gate /v1/*, and the announce step -- which exists because
a Kaggle kernel's log is unreadable while it runs -- must publish the URL
without taking the server down when the rendezvous is unreachable.
"""

from __future__ import annotations

import json
import sys
import threading
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
