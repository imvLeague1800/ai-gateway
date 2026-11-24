"""Client and metric parser adapted from the prior dual-Qwen harness."""
import json
import os
import re
import time
import uuid
from typing import Any

import httpx

ROUTER_API = os.getenv("ROUTER_API", "http://127.0.0.1:18100")
ROUTER_METRICS = os.getenv("ROUTER_METRICS", "http://127.0.0.1:19100/metrics")
MODEL = os.getenv("MODEL", "qwen3.6-35b-a3b-nvfp4")
ENGINES = {
    "qwen15": os.getenv("WORKER_A", "http://10.169.25.15:8000"),
    "qwen16": os.getenv("WORKER_B", "http://10.169.25.16:8000"),
}
THINK = {"enable_thinking": False}

def parse_metrics(text: str, names: set[str]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {name: {} for name in names}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        for name in names:
            if not line.startswith(name):
                continue
            match = re.match(re.escape(name) + r"\{(.*?)\} ([0-9.eE+-]+)", line)
            if not match:
                continue
            labels = {}
            for part in match.group(1).split(","):
                if "=" in part:
                    key, value = part.split("=", 1)
                    labels[key] = value.strip('"')
            result[name][json.dumps(labels, sort_keys=True)] = float(match.group(2))
    return result


async def router_snapshot(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    try:
        text = (await client.get(ROUTER_METRICS, timeout=10)).text
    except Exception:
        return {}
    return parse_metrics(
        text,
        {
            "vllm_router_processed_requests_total",
            "vllm_router_running_requests",
            "vllm_router_worker_health",
            "vllm_router_prefix_routing_decisions_total",
        },
    )


async def engine_snapshot(client: httpx.AsyncClient) -> dict[str, dict[str, float]]:
    out = {}
    for name, url in ENGINES.items():
        try:
            text = (await client.get(url + "/metrics", timeout=10)).text
        except Exception:
            out[name] = {}
            continue
        values = parse_metrics(
            text,
            {
                "vllm:prefix_cache_hits_total",
                "vllm:prefix_cache_queries_total",
                "vllm:num_requests_running",
                "vllm:num_requests_waiting",
                "vllm:kv_cache_usage_perc",
                "vllm:num_preemptions_total",
            },
        )
        out[name] = {name: sum(series.values()) for name, series in values.items() if series}
    return out


def check_quality(spec: dict, content: str, tool_calls: list[dict]) -> bool:
    if spec["quality_type"] == "tool":
        return any(
            (call.get("function") or {}).get("name") == "get_service_status"
            and "checkout" in ((call.get("function") or {}).get("arguments") or "")
            for call in tool_calls
        )
    if spec["quality_type"] == "regex_version":
        return bool(re.search(r"0\.07", content)) and "2.4.1" in content
    if spec["quality_type"] == "regex":
        return bool(re.search(spec["quality"], content))
    if spec["quality_type"] == "code":
        return spec["quality"].lower() in content.lower()
    return False


async def send_one(client: httpx.AsyncClient, spec: dict, *, router_api: str = ROUTER_API, on_headers=None, on_response_headers=None) -> dict:
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": spec["messages"],
        "max_tokens": spec.get("max_tokens", 1024),
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": THINK,
    }
    if spec.get("tools"):
        body["tools"] = spec["tools"]
    if spec.get("user"):
        body["user"] = spec["user"]
    started = time.perf_counter()
    first_at = None
    last_content_at = None
    content: list[str] = []
    tool_calls: dict[int, dict] = {}
    usage: dict = {}
    status = None
    error = None
    done = False
    finish_reason = None
    events = []
    request_id = uuid.uuid4().hex
    try:
        async with client.stream("POST", router_api + "/v1/chat/completions", json=body, headers={"x-request-id": request_id}) as response:
            status = response.status_code
            if on_headers is not None:
                on_headers()
            if on_response_headers is not None:
                on_response_headers(dict(response.headers))
            if status != 200:
                error = (await response.aread()).decode(errors="replace")[:1000]
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    events.append(payload)
                    if payload == "[DONE]":
                        done = True
                        break
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices", []):
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            now = time.perf_counter()
                            if first_at is None:
                                first_at = now
                            last_content_at = now
                            content.append(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            slot = tool_calls.setdefault(
                                idx,
                                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
                            if first_at is None and (fn.get("name") or fn.get("arguments")):
                                first_at = time.perf_counter()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = status or 0
    ended = time.perf_counter()
    text = "".join(content)
    record = {
        "id": spec["id"],
        "kind": spec["kind"],
        "status": status,
        "done": done,
        "ttft_ms": round((first_at - started) * 1000, 2) if first_at else None,
        "complete_ms": round((ended - started) * 1000, 2),
        "content": text,
        "request_id": request_id,
        "events": events,
        "finish_reason": finish_reason,
        "started": started,
        "ended": ended,
        "first_at": first_at,
        "last_at": last_content_at,
        "tool_calls": list(tool_calls.values()),
        "usage": usage,
        "error": error,
        "quality": check_quality(spec, text, list(tool_calls.values())) if status == 200 and done else False,
        "budget_ms": spec["budget_ms"],
    }
    if last_content_at and first_at and last_content_at > first_at:
        record["output_tps"] = round((usage.get("completion_tokens") or 0) / ((last_content_at - first_at) or 1), 3)
    else:
        record["output_tps"] = None
    if status == 200 and not done:
        record["state"] = "stream_interrupted"
    elif status == 429:
        record["state"] = "rejected"
    elif status != 200:
        record["state"] = "timeout" if error and "Timeout" in error else "http_error"
    elif finish_reason == "length":
        record["state"] = "truncated"
    elif not record["quality"] or first_at is None:
        record["state"] = "quality_failure"
    else:
        record["state"] = "success"
    return record
