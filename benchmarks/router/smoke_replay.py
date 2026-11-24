"""Replay the frozen 16-session smoke subset through one Router policy."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx

import transport

BASE = Path(__file__).resolve().parent
ROUTER_API = "http://127.0.0.1:18100"
POLICIES = ("power_of_two", "cache_aware")


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return round(ordered[index], 2)


def sha_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_variant(case: dict, old: str, new: str) -> dict:
    derived = copy.deepcopy(case)
    derived["id"] = case["id"] + "-smoke-variant"
    for message in derived["messages"]:
        content = message.get("content")
        if isinstance(content, str) and old in content:
            message["content"] = content.replace(old, new)
    if isinstance(derived.get("quality"), str):
        derived["quality"] = derived["quality"].replace(old, new)
    derived["derived_from"] = case["id"]
    derived["variant"] = "replace_recall"
    return derived


def load_specs() -> tuple[dict, list[dict]]:
    smoke = json.loads((BASE / "smoke.json").read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256((BASE / "manifest.json").read_bytes()).hexdigest()
    if smoke["source_manifest_sha256"] != manifest_hash:
        raise SystemExit(f"smoke source manifest hash mismatch: {smoke['source_manifest_sha256']} != {manifest_hash}")
    if smoke["cases_sha256"] != hashlib.sha256((BASE / "cases.jsonl").read_bytes()).hexdigest():
        raise SystemExit("smoke cases hash mismatch")
    if smoke["sessions_sha256"] != hashlib.sha256((BASE / "sessions.jsonl").read_bytes()).hexdigest():
        raise SystemExit("smoke sessions hash mismatch")
    cases = {row["id"]: row for row in read_jsonl(BASE / "cases.jsonl")}
    sessions = {row["id"]: row for row in read_jsonl(BASE / "sessions.jsonl")}
    specs = []
    for item in smoke["selection"]:
        source = sessions[item["source_id"]]
        source_steps = [(s["case"], s["parent"], s["gap_s"]) for s in source["steps"]]
        smoke_steps = [(s["case"], s["parent"], s["gap_s"]) for s in item["steps"]]
        if source_steps != smoke_steps:
            raise SystemExit(f"session step mismatch: {item['source_id']}")
        steps = []
        for step in item["steps"]:
            case = copy.deepcopy(cases[step["case"]])
            if item.get("variant") == "replace_recall" and step["parent"] is not None:
                case = build_variant(case, item["old_code"], item["new_code"])
            case["id"] = f"{item['session_id']}-{len(steps)}"
            steps.append({"case": case, "parent": step["parent"], "gap_s": step["gap_s"]})
        specs.append({**item, "steps": steps})
    if sum(len(item["steps"]) for item in specs) != smoke["expected_requests"]:
        raise SystemExit("smoke request count mismatch")
    return smoke, specs


def cache_delta(before: dict, after: dict) -> dict:
    delta = {}
    for engine in sorted(set(before) | set(after)):
        b = before.get(engine, {})
        a = after.get(engine, {})
        hits = a.get("vllm:prefix_cache_hits_total", 0) - b.get("vllm:prefix_cache_hits_total", 0)
        queries = a.get("vllm:prefix_cache_queries_total", 0) - b.get("vllm:prefix_cache_queries_total", 0)
        delta[engine] = {"hits": hits, "queries": queries,
                         "ratio": round(hits / queries, 4) if queries > 0 else None}
    return delta


def worker_delta(before: dict, after: dict) -> dict[str, float]:
    old = before.get("vllm_router_processed_requests_total", {})
    new = after.get("vllm_router_processed_requests_total", {})
    out = {}
    for labels, value in new.items():
        delta = value - old.get(labels, 0.0)
        if delta:
            out[json.loads(labels)["worker"]] = delta
    return out


async def direct_health(client: httpx.AsyncClient, url: str) -> int:
    try:
        return (await client.get(url + "/health", timeout=10)).status_code
    except Exception:
        return 0


async def run_one(client: httpx.AsyncClient, spec: dict) -> dict:
    before_engines = await transport.engine_snapshot(client)
    before_router = await transport.router_snapshot(client)
    result = await transport.send_one(client, spec)
    after_router = await transport.router_snapshot(client)
    after_engines = await transport.engine_snapshot(client)
    result["worker_delta"] = worker_delta(before_router, after_router)
    result["worker"] = next(iter(result["worker_delta"])) if len(result["worker_delta"]) == 1 else None
    result["cache_delta"] = cache_delta(before_engines, after_engines)
    result["protocol_ok"] = bool(result["status"] == 200 and result["done"] and result["finish_reason"] != "length")
    result["messages_sha256"] = sha_json(spec["messages"])
    result["prompt_tokens"] = (result.get("usage") or {}).get("prompt_tokens")
    result["output_tokens"] = (result.get("usage") or {}).get("completion_tokens")
    result["content"] = result.get("content", "")[:6000]
    result.pop("events", None)
    return result


def summarize(policy: str, rows: list[dict], health: dict, warmup: list[dict]) -> dict:
    groups = {}
    for group in ("qa", "agent", "rag", "rag_recall"):
        selected = [r for r in rows if r["group"] == group]
        state = Counter(r["state"] for r in selected)
        ttft = [r["ttft_ms"] for r in selected if r.get("ttft_ms") is not None]
        complete = [r["complete_ms"] for r in selected if r.get("complete_ms") is not None]
        groups[group] = {
            "requests": len(selected),
            "states": dict(state),
            "protocol_ok": sum(1 for r in selected if r.get("protocol_ok")),
            "quality_ok": sum(1 for r in selected if r.get("quality")),
            "ttft_ms": {"p50": pct(ttft, 0.5), "p95": pct(ttft, 0.95), "max": round(max(ttft), 2) if ttft else None},
            "complete_ms": {"p50": pct(complete, 0.5), "p95": pct(complete, 0.95), "max": round(max(complete), 2) if complete else None},
            "output_tokens": sum(r.get("output_tokens") or 0 for r in selected),
        }
    warm = [r for r in rows if r.get("warm")]
    attributed = [r for r in warm if r.get("worker")]
    same = 0
    for r in attributed:
        parent = next((p for p in rows if p["session"] == r["session"] and p["step"] == r["step"] - 1), None)
        if parent and parent.get("worker") and parent["worker"] == r["worker"]:
            same += 1
    hits = sum(sum(e["hits"] for e in r["cache_delta"].values()) for r in rows if r.get("cache_delta"))
    queries = sum(sum(e["queries"] for e in r["cache_delta"].values()) for r in rows if r.get("cache_delta"))
    warm_hits = sum(sum(e["hits"] for e in r["cache_delta"].values()) for r in warm if r.get("cache_delta"))
    workers = Counter(r["worker"] for r in rows if r.get("worker"))
    recall = [r for r in rows if r["group"] == "rag_recall" and r.get("warm")]
    recall_new = sum(1 for r in recall if r.get("quality"))
    return {
        "policy": policy,
        "requests": len(rows),
        "states": dict(Counter(r["state"] for r in rows)),
        "protocol_ok": sum(1 for r in rows if r.get("protocol_ok")),
        "quality_ok": sum(1 for r in rows if r.get("quality")),
        "groups": groups,
        "warm": {"requests": len(warm), "attributed": len(attributed), "same_worker": same,
                 "cache_hits": warm_hits},
        "workers": dict(workers),
        "both_workers_routed": len(workers) >= 2,
        "health": health,
        "cache": {"hits": hits, "queries": queries,
                  "ratio": round(hits / queries, 4) if queries else None, "warm_hits": warm_hits},
        "recall": {"warm_requests": len(recall), "new_value_ok": recall_new},
        "warmup": [{"id": r["id"], "status": r["status"], "state": r["state"], "worker": r.get("worker")} for r in warmup],
        "policy_ok": bool(
            len(rows) == 28
            and all(r["state"] == "success" for r in rows)
            and all(r.get("protocol_ok") and r.get("quality") for r in rows)
            and health.get("qwen15") == 200 and health.get("qwen16") == 200
            and len(workers) >= 1
            and recall_new == 4
            and warm_hits > 0
        ),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=POLICIES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    smoke, specs = load_specs()
    started = time.perf_counter()
    namespace = "NS-" + uuid.uuid4().hex[:12]
    for session in specs:
        for step in session["steps"]:
            for message in step["case"]["messages"]:
                if isinstance(message.get("content"), str):
                    message["content"] = message["content"].replace("BENCH_NAMESPACE", namespace)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=60, pool=20),
                                 limits=limits, trust_env=False) as client:
        health = {name: await direct_health(client, url) for name, url in transport.ENGINES.items()}
        warmup = []
        for i in range(2):
            spec = {"id": f"smoke-warmup-{i}", "kind": "qa",
                    "messages": [{"role": "user", "content": f"Smoke warmup {args.policy} {i}. Reply only OK."}],
                    "tools": None, "quality": "OK", "quality_type": "code", "budget_ms": 5000}
            warmup.append(await run_one(client, spec))
        rows = []
        for session in specs:
            results_by_case = {}
            for index, step in enumerate(session["steps"]):
                case = step["case"]
                parent_case = step["parent"]
                if parent_case:
                    parent = results_by_case.get(parent_case) or rows[-1]
                    wait = max(0.0, parent["ended"] + step["gap_s"] - time.perf_counter())
                    if wait:
                        await asyncio.sleep(wait)
                row = await run_one(client, case)
                row.update({
                    "policy": args.policy,
                    "group": session["group"],
                    "session": session["session_id"],
                    "source_id": session["source_id"],
                    "case_id": case["id"],
                    "parent_case": parent_case,
                    "step": index,
                    "warm": bool(case["kind"].endswith("_warm")),
                    "variant": session.get("variant"),
                    "expected_quality": case.get("quality"),
                })
                results_by_case[case["id"]] = row
                if parent_case:
                    # The parent case ID is the original ID, which differs for derived variants.
                    results_by_case.setdefault(parent_case, row)
                rows.append(row)
                print(json.dumps({k: row.get(k) for k in ("session", "case_id", "state", "ttft_ms",
                                                          "complete_ms", "worker", "quality")}, ensure_ascii=True), flush=True)
        summary = summarize(args.policy, rows, health, warmup)
        summary["smoke_sha256"] = hashlib.sha256((BASE / "smoke.json").read_bytes()).hexdigest()
        summary["dataset_manifest_sha256"] = smoke["source_manifest_sha256"]
        summary["git"] = __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        summary["router"] = ROUTER_API
        summary["namespace"] = namespace
        summary["elapsed_s"] = round(time.perf_counter() - started, 2)
        (out / "requests.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=True) for r in rows) + "\n", encoding="utf-8")
        (out / "warmup.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=True) for r in warmup) + "\n", encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
