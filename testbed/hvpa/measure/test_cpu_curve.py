"""The in-cluster WP17 curve tool: its request and its log hand-off."""

from __future__ import annotations

import io
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "measure"))

import cpu_curve  # noqa: E402


class Work(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's name)
        self.send_response(200 if self.path == "/work" else 404)
        self.send_header("X-Work-Seconds", "0.012500")
        self.end_headers()

    def log_message(self, *args):
        return


def test_get_work_reports_wall_time_and_the_service_work_time():
    srv = HTTPServer(("127.0.0.1", 0), Work)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        seconds, body = cpu_curve.get_work(f"http://127.0.0.1:{srv.server_port}")("/completion", {}, 5.0)
    finally:
        srv.shutdown()
    assert seconds > 0 and body["timings"]["predicted_ms"] == 12.5


def test_emit_and_parse_round_trip_through_a_log():
    rows = [{f: "" for f in cpu_curve.lc.FIELDS} | {"phase": "baseline", "seq": 0, "latency_s": 0.05}]
    out = io.StringIO()
    cpu_curve.emit(rows, {"cpu": 0.5, "mu_pre_rps": 9.0}, out)
    log = "kubelet noise\n" + out.getvalue() + "trailing\n"
    meta, body = cpu_curve.parse(log)
    assert meta == {"cpu": 0.5, "mu_pre_rps": 9.0}
    assert body.splitlines()[0].split(",") == cpu_curve.lc.FIELDS and "baseline" in body
