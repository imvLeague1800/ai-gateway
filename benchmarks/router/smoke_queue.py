"""Short queue/cancel regression through the isolated Router."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

import transport

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "smoke"
POLICIES = ("power_of_two", "cache_aware")


def queue_snapshot(snapshot: dict) -> dict:
    return {
        engine: {
            "running": values.get("vllm:num_requests_running", 0),
            "waiting": values.get("vllm:num_requests_waiting", 0),
        }
        for engine, values in snapshot.items()
    }


async def wait_recovered(client: httpx.AsyncClient, timeout: float = 60) -> dict:
    started = time.perf_counter()
    last = None
    while time.perf_counter() - started < timeout:
        engines = queue_snapshot(await transport.engine_snapshot(client))
        router = await transport.router_snapshot(client)
        running = sum(s.get("running", 0) for s in engines.values())
        waiting = sum(s.get("waiting", 0) for s in engines.values())
        router_running = sum(router.get("vllm_router_running_requests", {}).values())
        last = {"engines": engines, "router_running": router_running}
        if running == 0 and waiting == 0 and router_running == 0:
            return {"recovered_s": round(time.perf_counter() - started, 2), **last, "recovered": True}
        await asyncio.sleep(1)
    return {"recovered_s": round(time.perf_counter() - started, 2), **(last or {}), "recovered": False}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=POLICIES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=60, pool=30),
                                 limits=limits, trust_env=False) as client:
        spec = {
            "id": "queue-long",
            "kind": "qa",
            "messages": [{"role": "user", "content": "Write 120 numbered checklist items, one per line."}],
            "tools": None,
            "quality": "1",
            "quality_type": "code",
            "budget_ms": 60000,
        }
        headers_seen = [False] * 12
        tasks = []
        for i in range(12):
            tasks.append(asyncio.create_task(transport.send_one(
                client, dict(spec, id=f"queue-{i}"),
                on_headers=lambda i=i: headers_seen.__setitem__(i, True))))
        await asyncio.sleep(1.2)
        before = queue_snapshot(await transport.engine_snapshot(client))
        router_before = await transport.router_snapshot(client)
        headers_before_first_cancel = list(headers_seen)
        tasks[-1].cancel()
        await asyncio.sleep(1.0)
        after_waiting_cancel = queue_snapshot(await transport.engine_snapshot(client))
        headers_before_second_cancel = list(headers_seen)
        tasks[0].cancel()
        await asyncio.sleep(1.0)
        after_generating_cancel = queue_snapshot(await transport.engine_snapshot(client))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        rows = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                rows.append({"id": f"queue-{i}", "cancelled": True, "error": type(result).__name__})
            else:
                rows.append({k: result.get(k) for k in ("id", "status", "state", "ttft_ms", "complete_ms",
                                                        "output_tokens", "finish_reason", "error")})
        recovery = await wait_recovered(client)
        summary = {
            "policy": args.policy,
            "requests": 12,
            "queue_before_cancel": before,
            "queue_after_waiting_cancel": after_waiting_cancel,
            "queue_after_generating_cancel": after_generating_cancel,
            "router_running_before": router_before.get("vllm_router_running_requests", {}),
            "cancelled_ids": [r["id"] for r in rows if r.get("cancelled")],
            "cancel_observation": {
                "queue-11_headers_seen": headers_before_first_cancel[-1],
                "queue-0_headers_seen": headers_before_second_cancel[0],
                "headers_seen_before_first_cancel": headers_before_first_cancel,
                "headers_seen_before_second_cancel": headers_before_second_cancel,
            },
            "completed": sum(1 for r in rows if not r.get("cancelled")),
            "states": {},
            "recovery": recovery,
        }
        states = {}
        for row in rows:
            if not row.get("cancelled"):
                states[row.get("state")] = states.get(row.get("state"), 0) + 1
        summary["states"] = states
        (out / "queue-requests.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=True) for r in rows) + "\n", encoding="utf-8")
        (out / "queue-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
