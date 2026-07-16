"""Minimal OpenAI-compatible mock backend for the wire-attack harness.

The gateway calls its LLM provider on a cache MISS. In the wire attack we do
not want a real model (external key, network) — we want a *miss* to take a
realistic, stable amount of wall time so the hit-vs-miss timing gap the attack
reads is genuine rather than a connection error. This server answers
POST /v1/chat/completions after MOCK_LLM_DELAY_MS (default 250 ms) with a
valid completion. Deterministic, stdlib only.

    MOCK_LLM_DELAY_MS=250 python mock_llm.py --port 11434
"""

from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY_S = float(os.environ.get("MOCK_LLM_DELAY_MS", "250")) / 1000.0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        time.sleep(DELAY_S)  # a model call costs real wall time; that is the miss signal
        body = json.dumps({
            "id": "mock-cmpl",
            "object": "chat.completion",
            "model": "mock",
            "choices": [{"index": 0, "message": {"role": "assistant",
                        "content": "mock completion"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=11434)
    args = ap.parse_args()
    print(f"mock LLM on :{args.port}, miss delay {DELAY_S*1000:.0f} ms", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
