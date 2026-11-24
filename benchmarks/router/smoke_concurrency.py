"""Direct single-replica concurrency smoke: 1-4 lanes, bursts and sustained load."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx

import transport
import scenario_stats as stats

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "smoke" / "concurrency"
DIRECT = "http://10.169.25.15:8000"
SCENARIOS = ("agent", "rag_cold", "rag_warm")
METRIC_NAMES = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:prefix_cache_hits_total",
    "vllm:prefix_cache_queries_total",
    "vllm:kv_cache_usage_perc",
    "vllm:num_preemptions_total",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
}


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return round(ordered[index], 3)


def load_case(case_id: str) -> dict:
    cases = {row["id"]: row for row in read_jsonl(BASE / "cases.jsonl")}
    return copy.deepcopy(cases[case_id])


def unique_code(tag: str) -> str:
    return "SMOKE-" + hashlib.sha256(tag.encode()).hexdigest()[:10].upper()


def with_long_output(case: dict, marker: str, code: str | None = None) -> dict:
    """Derive a long-output case while preserving the source input shape."""
    derived = copy.deepcopy(case)
    derived["id"] = f"smoke-{marker}"
    text = derived["messages"][-1]["content"]
    text = text.replace("BENCH_NAMESPACE", marker)
    if code:
        text = text.replace("RAG-000-AAAAAA", code)
        text = text.replace("Document:\n", f"Document:\nSMOKE-PREFIX {marker}\n", 1)
        text += ("\n\nFirst reply with the maintenance access code only. Then start a new line with "
                 "CHECKLIST and write 40 numbered operational checklist items, one per line. "
                 "Do not use tools.")
        derived["quality"] = code
    else:
        text = ("Do not call any tools. Start with CHECKLIST and write 40 numbered operational "
                "checklist items, one per line.\n\nContext marker: " + marker + "\n\n" + text[:8000])
        derived["quality"] = "CHECKLIST"
    derived["messages"][-1]["content"] = text
    derived["quality_type"] = "code"
    derived["budget_ms"] = 30000
    derived["smoke_marker"] = marker
    return derived


def agent_case(marker: str) -> dict:
    # Keep the short Agent system/context and tool schema; replace only the final user turn.
    base = load_case("agent-000-cold")
    return with_long_output(base, marker, None)


def rag_cold_case(marker: str, code: str) -> dict:
    base = load_case("rag-000-cold")
    return with_long_output(base, marker, code)


def rag_warm_pair(marker: str, code: str) -> tuple[dict, dict]:
    cold = rag_cold_case(marker, code)
    pre = copy.deepcopy(cold)
    pre["id"] = f"prewarm-{marker}"
    pre["messages"] = copy.deepcopy(cold["messages"])
    pre["messages"][-1] = {
        "role": "user",
        "content": cold["messages"][-1]["content"].split("\n\nFirst reply with the maintenance access code only.")[0],
    }
    warm_messages = copy.deepcopy(cold["messages"]) + [
        {"role": "assistant", "content": code},
        {"role": "user", "content": "Repeat the maintenance access code. Then start a new line with CHECKLIST "
                                     "and write 40 numbered operational checklist items, one per line."},
    ]
    warm = copy.deepcopy(cold)
    warm["id"] = f"warm-{marker}"
    warm["kind"] = "rag_warm"
    warm["messages"] = warm_messages
    warm["quality"] = code
    return pre, warm


async def direct_snapshot(client: httpx.AsyncClient) -> dict:
    try:
        text = (await client.get(DIRECT + "/metrics", timeout=10)).text
    except Exception:
        return {}
    values = transport.parse_metrics(text, METRIC_NAMES)
    return {name: sum(series.values()) for name, series in values.items() if series}


def cache_ratio(before: dict, after: dict) -> dict:
    hits = after.get("vllm:prefix_cache_hits_total", 0) - before.get("vllm:prefix_cache_hits_total", 0)
    queries = after.get("vllm:prefix_cache_queries_total", 0) - before.get("vllm:prefix_cache_queries_total", 0)
    return {"hits": hits, "queries": queries, "ratio": round(hits / queries, 4) if queries > 0 else None}


def record(result: dict, *, scenario: str, concurrency: int, phase: str, round_no: int,
           lane: int, cache: dict) -> dict:
    result.update({
        "scenario": scenario,
        "concurrency": concurrency,
        "phase": phase,
        "round": round_no,
        "lane": lane,
        "cache_delta": cache,
        "output_tokens": (result.get("usage") or {}).get("completion_tokens"),
        "prompt_tokens": (result.get("usage") or {}).get("prompt_tokens"),
        "protocol_ok": bool(result.get("status") == 200 and result.get("done") and result.get("finish_reason") != "length"),
    })
    result["content"] = result.get("content", "")[:2500]
    result.pop("events", None)
    return result


async def send_direct(client: httpx.AsyncClient, spec: dict, *, capture_cache: bool = False) -> tuple[dict, dict]:
    before = await direct_snapshot(client) if capture_cache else None
    # A fresh client per request avoids both stale keep-alive sockets and pool reuse under
    # long 16K prefills. It is intentionally isolated from the metrics client.
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=900, write=60, pool=2),
                                 limits=limits, trust_env=False) as request_client:
        result = await transport.send_one(request_client, spec, router_api=DIRECT)
    if not capture_cache:
        return result, {}
    after = await direct_snapshot(client)
    return result, cache_ratio(before, after)


async def one_request(client: httpx.AsyncClient, spec: dict, *, capture_cache: bool = False, **meta) -> dict:
    result, cache = await send_direct(client, spec, capture_cache=capture_cache)
    return record(result, cache=cache, **meta)


async def burst(client: httpx.AsyncClient, specs: list[dict], *, scenario: str, concurrency: int,
                round_no: int) -> tuple[list[dict], dict]:
    before = await direct_snapshot(client)
    tasks = [asyncio.create_task(one_request(client, spec, scenario=scenario, concurrency=concurrency,
                                             phase="burst", round_no=round_no, lane=i))
             for i, spec in enumerate(specs)]
    rows = await asyncio.gather(*tasks)
    after = await direct_snapshot(client)
    return rows, cache_ratio(before, after)


async def sustain(client: httpx.AsyncClient, make_spec, *, scenario: str, concurrency: int,
                  duration: float, warmup: float = 5.0) -> dict:
    stop_launch = time.perf_counter() + warmup
    warm_tasks = []
    for lane in range(concurrency):
        warm_tasks.append(asyncio.create_task(one_request(
            client, make_spec(lane, f"warm-{lane}"), scenario=scenario, concurrency=concurrency,
            phase="sustain-warmup", round_no=0, lane=lane)))
    await asyncio.sleep(warmup)
    await asyncio.gather(*warm_tasks, return_exceptions=True)

    cache_before = await direct_snapshot(client)
    window_start = time.perf_counter()
    window_end = window_start + duration
    rows: list[dict] = []
    samples: list[dict] = []
    stop_sampling = asyncio.Event()

    async def sample():
        while not stop_sampling.is_set():
            snap = await direct_snapshot(client)
            samples.append({"at": round(time.perf_counter() - window_start, 2),
                            "running": snap.get("vllm:num_requests_running"),
                            "waiting": snap.get("vllm:num_requests_waiting")})
            await asyncio.sleep(0.5)

    sampler = asyncio.create_task(sample())

    async def loop(lane: int):
        index = 0
        while time.perf_counter() < window_end:
            spec = make_spec(lane, f"sustain-{lane}-{index}")
            row = await one_request(client, spec, scenario=scenario, concurrency=concurrency,
                                    phase="sustain", round_no=0, lane=lane)
            rows.append(row)
            index += 1

    loops = [asyncio.create_task(loop(lane)) for lane in range(concurrency)]
    await asyncio.gather(*loops, return_exceptions=True)
    stop_sampling.set()
    await sampler
    cache_after = await direct_snapshot(client)
    # All loops have stopped launching and returned after their last request completed.
    drain_s = max(0.0, time.perf_counter() - window_end)
    completed = [r for r in rows if r["started"] >= window_start and r["ended"] <= window_end]
    window_tokens = sum(r.get("output_tokens") or 0 for r in completed)
    run_values = [s["running"] for s in samples if s.get("running") is not None]
    wait_values = [s["waiting"] for s in samples if s.get("waiting") is not None]
    cache_window = cache_ratio(cache_before, cache_after)
    return {
        "scenario": scenario,
        "concurrency": concurrency,
        "window_s": duration,
        "window_start_epoch": window_start,
        "window_end_epoch": window_end,
        "warmup_s": warmup,
        "drain_s": round(drain_s, 2),
        "window_requests": len(completed),
        "window_output_tokens": window_tokens,
        "window_output_tps": round(window_tokens / duration, 3),
        "actual_concurrency_avg": round(statistics.mean(run_values), 2) if run_values else None,
        "actual_concurrency_max": max(run_values) if run_values else None,
        "waiting_max": max(wait_values) if wait_values else None,
        "samples": samples,
        "cache": cache_window,
        "requests": rows,
    }


def summarize_rows(scenario: str, concurrency: int, rows: list[dict], sustained: dict) -> dict:
    ttft = [r["ttft_ms"] for r in rows if r.get("ttft_ms") is not None]
    tps = [r["output_tps"] for r in rows if r.get("output_tps")]
    out_tokens = [r.get("output_tokens") or 0 for r in rows]
    states = {}
    for row in rows:
        states[row.get("state")] = states.get(row.get("state"), 0) + 1
    return {
        "scenario": scenario,
        "concurrency": concurrency,
        "samples": len(rows),
        "ttft_ms": {"p50": pct(ttft, 0.5), "max": round(max(ttft), 2) if ttft else None},
        "per_lane_tps": {"median": round(statistics.median(tps), 3) if tps else None,
                         "p10": pct(tps, 0.1)},
        "output_tokens": {"min": min(out_tokens) if out_tokens else None,
                          "median": statistics.median(out_tokens) if out_tokens else None,
                          "max": max(out_tokens) if out_tokens else None},
        "burst_window_tps": round(sum(r.get("output_tokens") or 0 for r in rows) /
                                  max(1e-9, max(r["ended"] for r in rows) - min(r["started"] for r in rows)), 3),
        "sustained": {k: v for k, v in sustained.items() if k not in ("requests", "samples")},
        "states": states,
        "failures": sum(1 for r in rows if r.get("state") not in ("success", "truncated")),
        "truncated": sum(1 for r in rows if r.get("state") == "truncated"),
        "quality_ok": sum(1 for r in rows if r.get("quality")),
        "protocol_ok": sum(1 for r in rows if r.get("protocol_ok")),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=900, write=60, pool=20),
                                 limits=limits, trust_env=False) as client:
        health = 0
        try:
            health = (await client.get(DIRECT + "/health", timeout=10)).status_code
        except Exception:
            pass
        baseline = await direct_snapshot(client)
        warm_specs: list[dict] = []
        if args.scenario == "rag_warm":
            for lane in range(args.concurrency):
                marker = f"smoke-{args.scenario}-c{args.concurrency}-lane{lane}-{uuid.uuid4().hex[:6]}"
                pre, warm = rag_warm_pair(marker, unique_code(marker))
                pre["id"] = f"prewarm-{marker}"
                pre["kind"] = "rag_cold"
                result, _ = await send_direct(client, pre)
                if result.get("state") != "success":
                    raise RuntimeError(f"prewarm failed lane {lane}: {result.get('state')} {result.get('error')}")
                assistant = result.get("content") or warm["quality"]
                warm["messages"] = copy.deepcopy(warm["messages"][:-2]) + [
                    {"role": "assistant", "content": assistant},
                    {"role": "user", "content": "Repeat the maintenance access code. Then start a new line with "
                                                "CHECKLIST and write 40 numbered operational checklist items, one per line."},
                ]
                warm_specs.append(warm)
        else:
            for lane in range(args.concurrency):
                marker = f"smoke-{args.scenario}-c{args.concurrency}-lane{lane}-{uuid.uuid4().hex[:6]}"
                if args.scenario == "agent":
                    warm_specs.append(agent_case(marker))
                else:
                    code = unique_code(marker)
                    warm_specs.append(rag_cold_case(marker, code))

        def make_spec(lane: int, tag: str):
            marker = f"smoke-{args.scenario}-c{args.concurrency}-{tag}-{uuid.uuid4().hex[:6]}"
            if args.scenario == "agent":
                return agent_case(marker)
            if args.scenario == "rag_cold":
                return rag_cold_case(marker, unique_code(marker))
            # Warm: rotate the prewarmed lane; each request reuses a hot prefix.
            return copy.deepcopy(warm_specs[lane % len(warm_specs)])

        # One unmeasured burst, then three measured synchronous bursts.
        _, warmup_cache = await burst(client, [make_spec(i, "warmup") for i in range(args.concurrency)],
                                      scenario=args.scenario, concurrency=args.concurrency, round_no=-1)
        burst_rows = []
        burst_caches = []
        for round_no in range(1, args.rounds + 1):
            rows, phase_cache = await burst(client, [make_spec(i, f"r{round_no}") for i in range(args.concurrency)],
                                            scenario=args.scenario, concurrency=args.concurrency, round_no=round_no)
            burst_rows.extend(rows)
            burst_caches.append(phase_cache)
        sustained = await sustain(client, make_spec, scenario=args.scenario,
                                  concurrency=args.concurrency, duration=args.duration)
        all_rows = burst_rows + sustained["requests"]
        summary = summarize_rows(args.scenario, args.concurrency, all_rows, sustained)
        summary["health"] = health
        summary["baseline"] = baseline
        summary["burst_samples"] = len(burst_rows)
        summary["sustained_requests"] = len(sustained["requests"])
        summary["burst_cache"] = burst_caches
        summary["warmup_burst_cache"] = warmup_cache
        summary["smoke_sha256"] = hashlib.sha256((BASE / "smoke.json").read_bytes()).hexdigest()
        (args.output / "requests.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=True) for r in all_rows) + "\n", encoding="utf-8")
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
        # Keep burst rows separate so sustained window accounting is unambiguous.
        (args.output / "burst.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=True) for r in burst_rows) + "\n", encoding="utf-8")
        (args.output / "sustained.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=True) for r in sustained["requests"]) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
