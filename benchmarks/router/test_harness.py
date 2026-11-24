"""Offline checks for timing, state classification, scoring and cache math."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

import replay
import transport

BASE = Path(__file__).resolve().parent


MOCK_CASE = {"case": "success"}


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _sse(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        case = MOCK_CASE["case"]
        if case == "429":
            body = b'{"error":{"message":"rate limited"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if case == "500":
            body = b'{"error":{"message":"server error"}}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if case == "interrupted":
            self._sse(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n')
            self.close_connection = True
            return
        finish = "length" if case == "truncated" else "stop"
        content = "WRONG" if case == "quality" else "OK"
        first = json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": None}]})
        final = json.dumps(
            {"choices": [{"delta": {}, "finish_reason": finish}], "usage": {"completion_tokens": 1, "prompt_tokens": 10, "total_tokens": 11}}
        )
        self._sse(f"data: {first}\n\ndata: {final}\n\ndata: [DONE]\n\n".encode())

    def log_message(self, *args) -> None:
        pass


async def run_cases() -> dict:
    server = ThreadingHTTPServer(("127.0.0.1", 18081), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old = transport.ROUTER_API
    transport.ROUTER_API = "http://127.0.0.1:18081"
    outcomes = {}
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            for case in ("success", "429", "500", "interrupted", "truncated", "quality"):
                MOCK_CASE["case"] = case
                spec = {
                    "id": f"mock-{case}",
                    "kind": "qa",
                    "messages": [{"role": "user", "content": "mock"}],
                    "tools": None,
                    "quality": r"\bOK\b",
                    "quality_type": "regex",
                    "budget_ms": 2000,
                }
                result = await transport.send_one(client, spec, router_api="http://127.0.0.1:18081")
                outcomes[case] = {
                    "state": result["state"],
                    "status": result["status"],
                    "ttft_positive": (result["ttft_ms"] or 0) > 0,
                    "done": result["done"],
                    "quality": result["quality"],
                    "finish_reason": result["finish_reason"],
                    "content": result["content"][:20],
                }
    finally:
        transport.ROUTER_API = old
        server.shutdown()
    return outcomes


def main() -> None:
    cases, sessions, profiles = replay.load()
    checks = {"dataset": {"cases": len(cases), "sessions": len(sessions), "profiles_seed": profiles["seed"]}}
    checks["timing_and_states"] = asyncio.run(run_cases())
    before = {"engines": {k: {replay.HITS: 100.0, replay.QUERIES: 200.0} for k in transport.ENGINES}}
    after = {"engines": {k: {replay.HITS: 160.0, replay.QUERIES: 300.0} for k in transport.ENGINES}}
    cache = replay.cache_ratio([before, after])
    checks["cache"] = cache
    checks["percentile"] = {"p50": replay.percentile([1, 2, 3, 4], 0.50), "p95": replay.percentile([1, 2, 3, 4], 0.95)}
    assert checks["timing_and_states"]["success"]["state"] == "success"
    assert checks["timing_and_states"]["429"]["state"] == "rejected"
    assert checks["timing_and_states"]["500"]["state"] == "http_error"
    assert checks["timing_and_states"]["interrupted"]["state"] == "stream_interrupted"
    assert checks["timing_and_states"]["truncated"]["state"] == "truncated"
    assert checks["timing_and_states"]["quality"]["state"] == "quality_failure"
    assert checks["timing_and_states"]["success"]["ttft_positive"] is True
    assert checks["cache"]["ratio"] == 0.6
    assert checks["percentile"]["p50"] == 3
    (BASE / "test-results.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
