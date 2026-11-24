"""Open-loop short-Agent + 17.5K cold-RAG replay through the first-version Scheduler."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
sys.path.insert(0, str(BASE.parent))
import transport
import smoke_concurrency as smoke
import scenario_stats as stats

DEFAULT_URL = "http://127.0.0.1:18080"
ENGINE = os.getenv("WORKER_A", "http://10.169.25.15:8000")
METRIC_NAMES = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:prefix_cache_hits_total",
    "vllm:prefix_cache_queries_total",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:num_preemptions_total",
    "vllm:kv_cache_usage_perc",
}


def short_agent_case(marker: str) -> dict:
    """Keep the Agent shape but trim the project context below 8192 prompt tokens."""
    base = smoke.load_case("agent-000-cold")
    system = base["messages"][0]["content"].splitlines()
    base["messages"][0]["content"] = "\n".join(system[:140]) + "\nProject context truncated for the short-Agent profile."
    case = smoke.with_long_output(base, marker, None)
    case["messages"][-1]["content"] = case["messages"][-1]["content"].replace(
        "write 40 numbered", "write 25 numbered"
    )
    case["kind"] = "short_agent"
    return case


def cold_rag_case(marker: str, code: str) -> dict:
    case = smoke.rag_cold_case(marker, code)
    case["messages"][-1]["content"] = case["messages"][-1]["content"].replace(
        "write 40 numbered", "write 25 numbered"
    )
    # A single early marker is not enough for vLLM's block cache: repeated document
    # blocks can still hit. Salt every archive record with a run-specific equal-length
    # token so the whole document is genuinely cold.
    tail = int(hashlib.sha256(marker.encode()).hexdigest()[:4], 16)
    salt = chr(ord("a") + (tail & 0xFF) % 26) + chr(ord("a") + ((tail >> 8) & 0xFF) % 26)
    case["messages"][-1]["content"] = case["messages"][-1]["content"].replace(
        "Archive record ", f"Archive re{salt}rd "
    )
    return case


def build_specs(run_tag: str, count: int) -> list[dict]:
    specs = []
    for index in range(count):
        marker = f"COLD-{run_tag}-{index:04d}"
        if index % 2 == 0:
            spec = short_agent_case(marker)
            spec["scenario"] = "short_agent"
        else:
            code = f"COLD-CODE-{run_tag}-{index:04d}"
            spec = cold_rag_case(marker, code)
            spec["scenario"] = "cold_rag"
        spec["user"] = f"cold-user-{index % 8}"
        spec["max_tokens"] = 512
        spec["budget_ms"] = 30000
        spec["index"] = index
        specs.append(spec)
    return specs


def parse_engine(text: str) -> dict:
    parsed = transport.parse_metrics(text, METRIC_NAMES)
    return {name: sum(series.values()) for name, series in parsed.items() if series}


async def engine_snapshot(client: httpx.AsyncClient) -> dict:
    try:
        return parse_engine((await client.get(ENGINE + "/metrics", timeout=10)).text)
    except Exception:
        return {}


def delta(before: dict, after: dict, name: str) -> float | None:
    if name not in before or name not in after:
        return None
    value = after[name] - before[name]
    return value if value >= 0 else None


async def send_request(client: httpx.AsyncClient, spec: dict, planned: float, run_tag: str,
                       limit: int, group: int, url: str) -> dict:
    headers = {}
    result = await transport.send_one(
        client, spec, router_api=url, on_response_headers=lambda values: headers.update(values)
    )
    queue_text = headers.get("x-gateway-queue-wait-seconds")
    queue_ms = round(float(queue_text) * 1000, 2) if queue_text not in (None, "") else None
    usage = result.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    result.update({
        "run_tag": run_tag,
        "limit": limit,
        "group": group,
        "scenario": spec["scenario"],
        "planned": planned,
        "arrival_delay_ms": round((result["started"] - planned) * 1000, 2),
        "gateway_headers": headers,
        "gateway_queue_wait_ms": queue_ms,
        "backend_id": headers.get("x-gateway-backend-id"),
        "user": spec.get("user"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": completion_tokens,
        "output_tps_after_first": stats.after_first_tps(usage, result.get("first_at"), result.get("last_at")),
        "engine_queue_wait_ms": None,  # vLLM exposes only aggregate waiting counters
        "cache_delta": None,  # concurrent requests cannot be attributed per request
        "content": result.get("content", "")[:2000],
    })
    result.pop("events", None)
    return result


def client_exception(spec: dict, planned: float, sent_at: float | None, exc: BaseException,
                     run_tag: str, limit: int, group: int) -> dict:
    now = time.perf_counter()
    started = sent_at if sent_at is not None else None
    return {
        "id": spec.get("id"),
        "scenario": spec.get("scenario"),
        "run_tag": run_tag, "limit": limit, "group": group,
        "planned": planned, "started": started, "ended": now,
        "arrival_delay_ms": round(((started if started is not None else planned) - planned) * 1000, 2),
        "state": "client_exception", "status": 0,
        "ttft_ms": None, "complete_ms": round((now - (started or planned)) * 1000, 2),
        "output_tokens": None, "output_tps_after_first": None,
        "gateway_queue_wait_ms": None, "backend_id": None,
        "user": spec.get("user"), "error": type(exc).__name__ + ": " + str(exc),
    }


def summarize(rows: list[dict], window_start: float, window_end: float, engine_before: dict,
              engine_after: dict, duration: float, waiting_samples: list[float]) -> dict:
    window_rows, outside_rows, unassignable = stats.assign_window(rows, window_start, window_end)
    scenario = stats.summarize_scenarios(window_rows)
    generation = delta(engine_before, engine_after, "vllm:generation_tokens_total")
    usage_tokens = sum((r.get("output_tokens") or 0) for r in window_rows
                       if r.get("ended") is not None and r["ended"] <= window_end)
    return {
        "window_s": duration,
        "window_requests": len(window_rows),
        "outside_requests": len(outside_rows),
        "unassignable_requests": len(unassignable),
        **scenario,
        "engine_generation_tokens_delta": generation,
        "engine_window_tps": round(generation / duration, 3) if generation is not None else None,
        "usage_tokens_completed_in_window": usage_tokens,
        "usage_window_tps": round(usage_tokens / duration, 3),
        "engine_cache": stats.endpoint_cache_delta(engine_before, engine_after),
        "engine_waiting_max": max(waiting_samples) if waiting_samples else None,
        "engine_queue_ms": {"status": "unavailable", "note": "aggregate waiting sampled; per-request queue not exposed by vLLM"},
        "drain_s": round(max((r["ended"] for r in rows if r.get("ended") is not None), default=window_end) - window_end, 2),
        "arrival_delay_ms": {
            "p50": stats._pct([r["arrival_delay_ms"] for r in window_rows
                               if r.get("arrival_delay_ms") is not None], 0.5),
            "p95": stats._pct([r["arrival_delay_ms"] for r in window_rows
                               if r.get("arrival_delay_ms") is not None], 0.95),
        },
        "users": dict(Counter(r.get("user") for r in window_rows)),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, choices=(1, 2), required=True)
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--warmup", type=float, default=15)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", default="")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run_tag = args.tag or f"l{args.limit}-g{args.group}-{int(time.time())}"
    total_span = args.warmup + args.duration
    count = int(total_span * args.rate) + 1
    specs = build_specs(run_tag, count)
    key = os.getenv("SCHEDULER_INTERNAL_API_KEY", "cold-rag-test-key")
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=900, write=60, pool=30),
                                 limits=limits, trust_env=False,
                                 headers={"Authorization": f"Bearer {key}"}) as client:
        ready = False
        for _ in range(60):
            try:
                if (await client.get(args.url + "/health/ready", timeout=3)).status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        if not ready:
            raise RuntimeError("scheduler is not ready")

        start = time.perf_counter() + 2
        window_start = start + args.warmup
        window_end = window_start + args.duration

        async def metrics_sampler():
            samples = []
            async with httpx.AsyncClient(timeout=10, trust_env=False) as metrics_client:
                await asyncio.sleep(max(0.0, window_start - time.perf_counter()))
                engine_before = await engine_snapshot(metrics_client)
                while time.perf_counter() < window_end:
                    snap = await engine_snapshot(metrics_client)
                    samples.append({"at": round(time.perf_counter() - window_start, 2),
                                    "running": snap.get("vllm:num_requests_running"),
                                    "waiting": snap.get("vllm:num_requests_waiting"),
                                    "kv": snap.get("vllm:kv_cache_usage_perc")})
                    await asyncio.sleep(1)
                engine_after = await engine_snapshot(metrics_client)
            return samples, engine_before, engine_after

        async def one(index: int, spec: dict):
            planned = start + index / args.rate
            sent_at = None
            try:
                await asyncio.sleep(max(0.0, planned - time.perf_counter()))
                sent_at = time.perf_counter()
                return await send_request(client, spec, planned, run_tag, args.limit, args.group, args.url)
            except Exception as exc:
                return client_exception(spec, planned, sent_at, exc, run_tag, args.limit, args.group)

        sampler_task = asyncio.create_task(metrics_sampler())
        tasks = [asyncio.create_task(one(i, spec)) for i, spec in enumerate(specs)]
        rows = await asyncio.gather(*tasks, return_exceptions=True)
        sampler, engine_before, engine_after = await sampler_task
        clean_rows = []
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                clean_rows.append(row)
            else:
                clean_rows.append(client_exception(
                    specs[index], start + index / args.rate, None, row, run_tag, args.limit, args.group
                ))
        waiting = [s["waiting"] for s in sampler if s.get("waiting") is not None]
        summary = summarize(clean_rows, window_start, window_end, engine_before, engine_after,
                            args.duration, waiting)
        summary.update({
            "run_tag": run_tag, "limit": args.limit, "group": args.group, "rate": args.rate,
            "warmup_s": args.warmup, "scheduler_url": args.url, "engine": ENGINE,
            "config": args.config or str(BASE / "backends-template.yaml"),
            "all_requests": len(clean_rows), "window_start": window_start, "window_end": window_end,
            "output_budget_tokens": 512,
        })
        (args.output / "requests.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=True) for r in clean_rows) + "\n", encoding="utf-8")
        (args.output / "metrics.jsonl").write_text(
            "\n".join(json.dumps(s, ensure_ascii=True) for s in sampler) + "\n", encoding="utf-8")
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
