"""Quick one-replica removal check for the smoke entry."""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from pathlib import Path

import httpx

import transport

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "smoke"
HOST = "10.169.25.16"
TARGET = f"http://{HOST}:8000"


def router_health(host: str) -> float | None:
    try:
        text = httpx.get("http://127.0.0.1:19100/metrics", timeout=3, trust_env=False).text
    except Exception:
        return None
    for line in text.splitlines():
        if line.startswith("vllm_router_worker_health") and host in line:
            return float(line.rsplit(" ", 1)[1])
    return None


async def direct_health() -> int:
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            return (await client.get(TARGET + "/health")).status_code
    except Exception:
        return 0


def worker_delta(before: dict, after: dict) -> dict:
    old = before.get("vllm_router_processed_requests_total", {})
    new = after.get("vllm_router_processed_requests_total", {})
    return {
        json.loads(labels)["worker"]: value - old.get(labels, 0.0)
        for labels, value in new.items()
        if value - old.get(labels, 0.0)
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    subprocess.run(["ssh", "-o", "BatchMode=yes", "dgx@" + HOST,
                    "cd /home/dgx/spark-vllm && docker compose --profile qwen stop qwen"],
                   check=True, capture_output=True, text=True, timeout=120)
    stop_done = time.perf_counter()
    detection = None
    while time.perf_counter() - started < 60:
        if router_health(HOST) == 0:
            detection = round(time.perf_counter() - started, 2)
            break
        await asyncio.sleep(0.5)
    direct = await direct_health()
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    probes = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=60, write=20, pool=10),
                                 limits=limits, trust_env=False) as client:
        for i in range(10):
            before = await transport.router_snapshot(client)
            result = await transport.send_one(client, {
                "id": f"failure-probe-{i}",
                "kind": "qa",
                "messages": [{"role": "user", "content": f"Failure probe {uuid.uuid4().hex[:8]}. Reply only OK."}],
                "tools": None, "quality": "OK", "quality_type": "code", "budget_ms": 5000,
            })
            after = await transport.router_snapshot(client)
            delta = worker_delta(before, after)
            probes.append({"status": result["status"], "state": result["state"], "ttft_ms": result["ttft_ms"],
                           "worker_delta": delta, "worker": next(iter(delta)) if len(delta) == 1 else None})
    summary = {
        "stopped": "qwen16",
        "stop_command_s": round(stop_done - started, 2),
        "router_detection_s": detection,
        "direct_health_after": direct,
        "probes": probes,
        "probe_success": sum(1 for p in probes if p["status"] == 200),
        "probe_workers": {},
        "all_200": all(p["status"] == 200 for p in probes),
    }
    for probe in probes:
        if probe["worker"]:
            summary["probe_workers"][probe["worker"]] = summary["probe_workers"].get(probe["worker"], 0) + 1
    (OUT / "failure-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
