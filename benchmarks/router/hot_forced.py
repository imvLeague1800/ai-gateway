"""Hot-prefix router reroute vs forced 'keep waiting on hot replica' probe."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import httpx

import transport
from prompts import filler

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "hot-forced-v12"
HOSTS = {"15": "http://10.169.25.15:8000", "16": "http://10.169.25.16:8000"}


def build_doc(code: str, salt: str) -> list[dict]:
    document = "\n".join(filler(i) for i in range(570))
    split = document.index("\n", 5000)
    recall = f"\nRecall block: the maintenance access code for this archive is {code}.\n"
    return [
        {"role": "system", "content": "You are a precise retrieval assistant. Answer only from the document."},
        {"role": "user", "content": f"Batch: {salt}.\nDocument:\n{document[:split]}{recall}{document[split:]}"
                                    "\n\nQuestion: What is the maintenance access code? Reply with the code only."},
    ]


def engine_snapshot(url: str) -> dict:
    text = httpx.get(url + "/metrics", timeout=10, trust_env=False).text
    out = {}
    for name in ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total"):
        out[name] = sum(float(line.rsplit(" ", 1)[1]) for line in text.splitlines() if line.startswith(name + "{"))
    return out


async def actual_worker(client: httpx.AsyncClient, result: dict) -> str | None:
    if len(result.get("router_worker_delta", {})) == 1:
        return next(iter(result["router_worker_delta"]))
    return None


def router_dispatch(snapshot: dict) -> dict[str, float]:
    return {
        json.loads(labels)["worker"]: value
        for labels, value in snapshot.get("vllm_router_processed_requests_total", {}).items()
    }


async def send_attributed(client, spec, router_api=None):
    before = await transport.router_snapshot(client)
    kwargs = {"router_api": router_api} if router_api else {}
    result = await transport.send_one(client, spec, **kwargs)
    after = await transport.router_snapshot(client)
    delta = {}
    for labels, value in after.get("vllm_router_processed_requests_total", {}).items():
        old = before.get("vllm_router_processed_requests_total", {}).get(labels, 0.0)
        if value != old:
            delta[json.loads(labels)["worker"]] = value - old
    result["router_worker_delta"] = delta
    result["actual_worker"] = next(iter(delta)) if len(delta) == 1 else None
    return result


async def warm_16(client, round_no: int) -> dict:
    """Warm .15 first, keep three long requests there, then force a cold doc to .16."""
    for attempt in range(4):
        salt_a = f"hot-forced-16a-{round_no}-{attempt}-{uuid.uuid4().hex[:6]}"
        code_a = f"HOT-A-{uuid.uuid4().hex[:6]}"
        cold_a = build_doc(code_a, salt_a)
        warm_a = cold_a + [{"role": "assistant", "content": code_a},
                           {"role": "user", "content": "Repeat the maintenance access code. Reply with the code only."}]
        long_a = cold_a + [{"role": "assistant", "content": code_a},
                           {"role": "user", "content": "Repeat the code, then write 100 numbered checklist items, one per line."}]
        first = await send_attributed(client, {"id": "force15-cold", "kind": "rag_cold", "messages": cold_a,
                                               "tools": None, "quality": code_a, "quality_type": "code", "budget_ms": 10000})
        if first.get("actual_worker") != HOSTS["15"]:
            continue
        loads = [asyncio.create_task(transport.send_one(client, {"id": f"force15-load-{i}", "kind": "rag_warm",
                                                                 "messages": long_a, "tools": None, "quality": code_a,
                                                                 "quality_type": "code", "budget_ms": 10000}))
                 for i in range(3)]
        await asyncio.sleep(1.0)
        salt_b = f"hot-forced-16b-{round_no}-{attempt}-{uuid.uuid4().hex[:6]}"
        code_b = f"HOT-B-{uuid.uuid4().hex[:6]}"
        cold_b = build_doc(code_b, salt_b)
        warm_b = cold_b + [{"role": "assistant", "content": code_b},
                           {"role": "user", "content": "Repeat the maintenance access code. Reply with the code only."}]
        long_b = cold_b + [{"role": "assistant", "content": code_b},
                           {"role": "user", "content": "Repeat the code, then write 100 numbered checklist items, one per line."}]
        second = await send_attributed(client, {"id": "force16-cold", "kind": "rag_cold", "messages": cold_b,
                                                "tools": None, "quality": code_b, "quality_type": "code", "budget_ms": 10000})
        await asyncio.gather(*loads, return_exceptions=True)
        if second.get("actual_worker") == HOSTS["16"]:
            return {"cold": second, "warm_messages": warm_b, "long_messages": long_b, "code": code_b}
    raise RuntimeError("could not warm target 16")


async def warm_target(client, target: str, round_no: int) -> dict:
    if target == "16":
        return await warm_16(client, round_no)
    salt = f"hot-forced-{target}-{round_no}-{uuid.uuid4().hex[:8]}"
    code = f"HOT-{target}-{uuid.uuid4().hex[:6]}"
    cold = build_doc(code, salt)
    warm = cold + [{"role": "assistant", "content": code},
                   {"role": "user", "content": "Repeat the maintenance access code. Reply with the code only."}]
    long = cold + [{"role": "assistant", "content": code},
                   {"role": "user", "content": "Repeat the code, then write 100 numbered checklist items, one per line."}]
    for attempt in range(6):
        result = await send_attributed(client, {"id": f"warm-cold-{attempt}", "kind": "rag_cold",
                                                "messages": cold, "tools": None, "quality": code,
                                                "quality_type": "code", "budget_ms": 10000})
        if result.get("actual_worker") == HOSTS[target]:
            return {"cold": result, "warm_messages": warm, "long_messages": long, "code": code}
        load = asyncio.create_task(transport.send_one(client, {"id": f"force-load-{attempt}", "kind": "rag_warm",
                                                               "messages": long, "tools": None, "quality": code,
                                                               "quality_type": "code", "budget_ms": 10000}))
        await asyncio.sleep(1.0)
        retry = await send_attributed(client, {"id": f"warm-cold-load-{attempt}", "kind": "rag_cold",
                                               "messages": cold, "tools": None, "quality": code,
                                               "quality_type": "code", "budget_ms": 10000})
        await asyncio.gather(load, return_exceptions=True)
        if retry.get("actual_worker") == HOSTS[target]:
            return {"cold": retry, "warm_messages": warm, "long_messages": long, "code": code}
    raise RuntimeError(f"could not warm target {target}")


async def round_target(policy: str, target: str, round_no: int) -> dict:
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=1800, write=120, pool=20), limits=limits, trust_env=False) as client:
        warmed = await warm_target(client, target, round_no)
        background = [asyncio.create_task(transport.send_one(client, {"id": f"hot-load-{i}", "kind": "rag_warm",
                                                                      "messages": warmed["long_messages"], "tools": None,
                                                                      "quality": warmed["code"], "quality_type": "code",
                                                                      "budget_ms": 10000}))
                      for i in range(3)]
        await asyncio.sleep(1.0)
        running = await transport.router_snapshot(client)
        auto = await send_attributed(client, {"id": "hot-auto", "kind": "rag_warm", "messages": warmed["warm_messages"],
                                              "tools": None, "quality": warmed["code"], "quality_type": "code", "budget_ms": 5000})
        before_engine = engine_snapshot(HOSTS[target])
        forced = await transport.send_one(client, {"id": "hot-forced", "kind": "rag_warm", "messages": warmed["warm_messages"],
                                                   "tools": None, "quality": warmed["code"], "quality_type": "code", "budget_ms": 5000},
                                          router_api=HOSTS[target])
        after_engine = engine_snapshot(HOSTS[target])
        await asyncio.gather(*background, return_exceptions=True)
        hits = after_engine["vllm:prefix_cache_hits_total"] - before_engine["vllm:prefix_cache_hits_total"]
        queries = after_engine["vllm:prefix_cache_queries_total"] - before_engine["vllm:prefix_cache_queries_total"]
        return {
            "policy": policy, "target": target, "round": round_no,
            "running_during_load": running.get("vllm_router_running_requests", {}),
            "auto": {k: auto.get(k) for k in ("actual_worker", "status", "state", "ttft_ms", "complete_ms")},
            "forced_direct": {k: forced.get(k) for k in ("status", "state", "ttft_ms", "complete_ms")},
            "forced_cache": {"hits": hits, "queries": queries, "ratio": round(hits / queries, 4) if queries else None},
        }


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("15", "16"), required=True)
    parser.add_argument("--round", type=int, default=1)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    subprocess = __import__("subprocess")
    subprocess.run(["ssh", "-o", "BatchMode=yes", "dgx@10.169.25.16",
                    "/home/dgx/spark-vllm/reports/dual-qwen/run-router.sh cache_aware"], check=True, capture_output=True, timeout=120)
    await asyncio.sleep(3)
    result = await round_target("cache_aware", args.target, args.round)
    path = OUT / f"cache_aware-qwen{args.target}-r{args.round}.json"
    path.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
