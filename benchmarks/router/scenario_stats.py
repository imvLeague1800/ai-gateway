"""Shared window attribution and per-scenario statistics for replayed requests."""
from __future__ import annotations

import statistics
from collections import Counter

from replay import percentile

SCENARIOS = ("short_agent", "cold_rag")
NON_FAILURE_STATES = {"success", "truncated"}


def _pct(values: list[float], q: float, digits: int = 2):
    value = percentile(values, q)
    return round(value, digits) if value is not None else None


def event_time(row: dict) -> float | None:
    """Actual send time when known; planned time for pre-send failures."""
    if row.get("started") is not None:
        return row["started"]
    if row.get("planned") is not None:
        return row["planned"]
    return None


def assign_window(rows: list[dict], start: float, end: float) -> tuple[list[dict], list[dict], list[dict]]:
    """Split rows into [start, end) membership, outside, and unassignable."""
    inside, outside, unassignable = [], [], []
    for row in rows:
        at = event_time(row)
        if at is None:
            unassignable.append(row)
        elif start <= at < end:
            inside.append(row)
        else:
            outside.append(row)
    return inside, outside, unassignable


def endpoint_cache_delta(before: dict, after: dict, hits_key: str = "vllm:prefix_cache_hits_total",
                         queries_key: str = "vllm:prefix_cache_queries_total") -> dict:
    """Counter delta; reset/regression/zero denominator stays unknown."""
    if hits_key not in before or hits_key not in after or queries_key not in before or queries_key not in after:
        return {"hits": None, "queries": None, "ratio": None}
    hits = after[hits_key] - before[hits_key]
    queries = after[queries_key] - before[queries_key]
    if hits < 0 or queries < 0:
        return {"hits": None, "queries": None, "ratio": None}
    return {"hits": hits, "queries": queries,
            "ratio": round(hits / queries, 4) if queries else None}


def after_first_tps(usage: dict, first_at: float | None, last_at: float | None) -> float | None:
    tokens = (usage or {}).get("completion_tokens")
    if tokens is None or first_at is None or last_at is None or last_at <= first_at:
        return None
    return round(max(0, tokens - 1) / (last_at - first_at), 3)


def summarize_scenarios(rows: list[dict]) -> dict:
    """One implementation used by online replay and offline analysis."""
    by_scenario = {}
    for scenario in SCENARIOS:
        selected = [r for r in rows if r.get("scenario") == scenario]
        ttft = [r["ttft_ms"] for r in selected if r.get("ttft_ms") is not None]
        complete = [r["complete_ms"] for r in selected if r.get("complete_ms") is not None]
        tps = [r.get("output_tps_after_first") for r in selected
               if r.get("output_tps_after_first") is not None]
        queue = [r.get("gateway_queue_wait_ms") for r in selected
                 if r.get("gateway_queue_wait_ms") is not None]
        out = [r["output_tokens"] for r in selected if r.get("output_tokens") is not None]
        states = Counter(r.get("state") for r in selected)
        by_scenario[scenario] = {
            "requests": len(selected),
            "success": states.get("success", 0),
            "states": dict(states),
            "failures": sum(1 for r in selected if r.get("state") not in NON_FAILURE_STATES),
            "ttft_ms": {"p50": _pct(ttft, 0.5), "p95": _pct(ttft, 0.95),
                        "max": round(max(ttft), 2) if ttft else None},
            "per_lane_tps": {"p50": _pct(tps, 0.5, 3), "p10": _pct(tps, 0.1, 3)},
            "gateway_queue_ms": {"p50": _pct(queue, 0.5), "p95": _pct(queue, 0.95)},
            "engine_queue_ms": {"status": "unavailable", "reason": "vLLM exposes aggregate waiting only"},
            "complete_ms": {"p50": _pct(complete, 0.5), "p95": _pct(complete, 0.95)},
            "output_tokens": {"min": min(out) if out else None,
                              "median": statistics.median(out) if out else None,
                              "max": max(out) if out else None},
        }
    unknown = [r for r in rows if r.get("scenario") not in SCENARIOS]
    return {
        "by_scenario": by_scenario,
        "unknown_scenario": len(unknown),
        "failures": sum(1 for r in rows if r.get("state") not in NON_FAILURE_STATES),
        "truncated": sum(1 for r in rows if r.get("state") == "truncated"),
        "states": dict(Counter(r.get("state") for r in rows)),
    }
