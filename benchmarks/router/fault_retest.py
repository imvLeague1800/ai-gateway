"""Fault/removal and recovery cycles for both policies and both Qwen replicas."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
import uuid
from pathlib import Path

import httpx

import transport

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "fault-v12"
ROUTER_API = "http://127.0.0.1:18100"
ROUTER_METRICS = "http://127.0.0.1:19100/metrics"
HOSTS = {"15": "10.169.25.15", "16": "10.169.25.16"}
POLICIES = ("power_of_two", "cache_aware")


def ssh(host: str, command: str) -> None:
    subprocess.run(["ssh", "-o", "BatchMode=yes", f"dgx@{HOSTS[host]}", command], check=True,
                   capture_output=True, text=True, timeout=120)


def restart(policy: str) -> None:
    subprocess.run(["ssh", "-o", "BatchMode=yes", "dgx@10.169.25.16",
                    f"/home/dgx/spark-vllm/reports/dual-qwen/run-router.sh {policy}"],
                   check=True, capture_output=True, text=True, timeout=120)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            models = httpx.get(ROUTER_API + "/v1/models", timeout=3, trust_env=False)
            metrics = httpx.get(ROUTER_METRICS, timeout=3, trust_env=False).text
            if models.status_code == 200 and metrics.count("vllm_router_worker_health") >= 2 and metrics.count(" 1") >= 2:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("router not ready")


def router_health(host: str) -> float | None:
    try:
        text = httpx.get(ROUTER_METRICS, timeout=3, trust_env=False).text
    except Exception:
        return None
    for line in text.splitlines():
        if line.startswith("vllm_router_worker_health") and HOSTS[host] in line:
            return float(line.rsplit(" ", 1)[1])
    return None


def running(host: str) -> float:
    try:
        text = httpx.get(ROUTER_METRICS, timeout=3, trust_env=False).text
    except Exception:
        return -1
    total = 0.0
    for line in text.splitlines():
        if line.startswith("vllm_router_running_requests") and HOSTS[host] in line:
            total += float(line.rsplit(" ", 1)[1])
    return total


async def direct_health(host: str) -> int:
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            return (await client.get(f"http://{HOSTS[host]}:8000/health")).status_code
    except Exception:
        return 0


async def send_probe(client: httpx.AsyncClient, index: int) -> dict:
    spec = {
        "id": f"fault-probe-{index}",
        "kind": "qa",
        "messages": [{"role": "user", "content": f"Fault probe {uuid.uuid4().hex[:8]}. Reply only OK."}],
        "tools": None,
        "quality": "OK",
        "quality_type": "code",
        "budget_ms": 5000,
    }
    before = await transport.router_snapshot(client)
    result = await transport.send_one(client, spec)
    after = await transport.router_snapshot(client)
    deltas = {}
    for labels, value in after.get("vllm_router_processed_requests_total", {}).items():
        old = before.get("vllm_router_processed_requests_total", {}).get(labels, 0.0)
        if value != old:
            deltas[json.loads(labels)["worker"]] = value - old
    return {"status": result["status"], "state": result["state"], "ttft_ms": result["ttft_ms"],
            "worker": next(iter(deltas)) if len(deltas) == 1 else None, "deltas": deltas}


async def monitor(host: str, stop: asyncio.Event, samples: list[dict]) -> None:
    while not stop.is_set():
        samples.append({"at": round(time.perf_counter(), 3), "router_health": router_health(host),
                        "running": running(host), "direct_health": await direct_health(host)})
        await asyncio.sleep(0.5)


async def cycle(policy: str, host: str, repeat: int) -> dict:
    restart(policy)
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=60, pool=20), limits=limits, trust_env=False) as client:
        long_spec = {
            "id": "fault-inflight",
            "kind": "qa",
            "messages": [{"role": "user", "content": "Write 120 numbered checklist items, one per line."}],
            "tools": None,
            "quality": r"\d",
            "quality_type": "regex",
            "budget_ms": 30000,
        }
        tasks = [asyncio.create_task(transport.send_one(client, dict(long_spec))) for _ in range(4)]
        await asyncio.sleep(1.0)
        before_inflight = {"15": running("15"), "16": running("16")}
        stop_monitor = asyncio.Event()
        samples: list[dict] = []
        monitor_task = asyncio.create_task(monitor(host, stop_monitor, samples))
        stop_started = time.perf_counter()
        await asyncio.to_thread(ssh, host, "cd /home/dgx/spark-vllm && docker compose --profile qwen stop qwen")
        stop_done = time.perf_counter()
        # Detection is observed by the monitor; wait until it sees health 0.
        detection_s = None
        while time.perf_counter() - stop_started < 60:
            if router_health(host) == 0:
                detection_s = round(time.perf_counter() - stop_started, 2)
                break
            await asyncio.sleep(0.25)
        probes_after_removal = [await send_probe(client, i) for i in range(20)]
        try:
            inflight = await asyncio.wait_for(asyncio.gather(*tasks), timeout=60)
        except asyncio.TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            inflight = [{"status": 0, "state": "stream_timeout", "ttft_ms": None, "error": "cancelled"} for _ in tasks]
        await asyncio.to_thread(ssh, host, "cd /home/dgx/spark-vllm && docker compose --profile qwen start qwen")
        recover_started = time.perf_counter()
        engine_health_s = None
        while time.perf_counter() - recover_started < 900:
            if await direct_health(host) == 200:
                engine_health_s = round(time.perf_counter() - recover_started, 2)
                break
            await asyncio.sleep(2)
        router_reaccept_s = None
        reaccept_started = time.perf_counter()
        while time.perf_counter() - reaccept_started < 60:
            if router_health(host) == 1:
                router_reaccept_s = round(time.perf_counter() - reaccept_started, 2)
                break
            await asyncio.sleep(0.5)
        probes_after_recovery = [await send_probe(client, 100 + i) for i in range(20)]
        stop_monitor.set()
        await monitor_task
        return {
            "policy": policy, "host": host, "repeat": repeat,
            "before_inflight_running": before_inflight,
            "stop_started": stop_started, "stop_command_s": round(stop_done - stop_started, 2),
            "detection_s": detection_s,
            "probes_after_removal": probes_after_removal,
            "inflight": [{k: r.get(k) for k in ("status", "state", "ttft_ms", "error")} for r in inflight],
            "engine_health_s": engine_health_s,
            "router_reaccept_s": router_reaccept_s,
            "probes_after_recovery": probes_after_recovery,
            "running_after": {"15": running("15"), "16": running("16")},
            "monitor_samples": samples,
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=POLICIES, required=True)
    parser.add_argument("--host", choices=("15", "16"), required=True)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    result = await cycle(args.policy, args.host, args.repeat)
    path = OUT / f"{args.policy}-qwen{args.host}-r{args.repeat}.json"
    path.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("policy", "host", "repeat", "detection_s", "engine_health_s", "router_reaccept_s", "before_inflight_running", "running_after")}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
