"""Aggregate paired cold-RAG admission runs and compare long limits 1 and 2."""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[2] / ".verification" / "dual-qwen" / "cold-rag" / "matrix"


def pct(values: list[float], q: float):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return round(ordered[index], 2)


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scenario_stats(rows: list[dict]) -> dict:
    result = {}
    for scenario in ("short_agent", "cold_rag"):
        selected = [r for r in rows if r.get("scenario") == scenario]
        ttft = [r["ttft_ms"] for r in selected if r.get("ttft_ms") is not None]
        complete = [r["complete_ms"] for r in selected if r.get("complete_ms") is not None]
        tps = [r["output_tps_after_first"] for r in selected if r.get("output_tps_after_first")]
        queue = [r["gateway_queue_wait_ms"] for r in selected if r.get("gateway_queue_wait_ms") is not None]
        out = [r.get("output_tokens") or 0 for r in selected]
        result[scenario] = {
            "requests": len(selected),
            "success": sum(1 for r in selected if r.get("state") == "success"),
            "states": dict(Counter(r.get("state") for r in selected)),
            "ttft_ms": {"p50": pct(ttft, 0.5), "p95": pct(ttft, 0.95), "max": round(max(ttft), 2) if ttft else None},
            "per_lane_tps": {"p50": pct(tps, 0.5), "p10": pct(tps, 0.1)},
            "gateway_queue_ms": {"p50": pct(queue, 0.5), "p95": pct(queue, 0.95)},
            "engine_queue_ms": {"status": "unavailable", "reason": "vLLM exposes aggregate waiting only"},
            "complete_ms": {"p50": pct(complete, 0.5), "p95": pct(complete, 0.95)},
            "output_tokens": {"min": min(out) if out else None,
                              "median": statistics.median(out) if out else None,
                              "max": max(out) if out else None},
        }
    return result


def main() -> None:
    runs = []
    for path in sorted(OUT.glob("g*-long*")):
        summary_path = path / "summary.json"
        requests_path = path / "requests.jsonl"
        if not (summary_path.exists() and requests_path.exists()):
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = read_jsonl(requests_path)
        window = [r for r in rows if summary["window_start"] <= r["started"] < summary["window_end"]]
        runs.append({
            "group": summary["group"], "limit": summary["limit"],
            "directory": str(path), "summary": summary,
            "rows": window, "all_rows": rows,
            "scenario": scenario_stats(window),
        })
    aggregate = {}
    for limit in (1, 2):
        selected = [r for r in runs if r["limit"] == limit]
        rows = [row for run in selected for row in run["rows"]]
        aggregate[str(limit)] = {
            "runs": len(selected),
            "groups": [run["group"] for run in selected],
            "scenario": scenario_stats(rows),
            "window_generation_tokens": sum((run["summary"].get("engine_generation_tokens_delta") or 0) for run in selected),
            "window_s": sum(run["summary"]["window_s"] for run in selected),
            "engine_window_tps": round(sum((run["summary"].get("engine_generation_tokens_delta") or 0) for run in selected) /
                                       sum(run["summary"]["window_s"] for run in selected), 3) if selected else None,
            "cache": {
                "hits": sum((run["summary"]["engine_cache"].get("hits") or 0) for run in selected),
                "queries": sum((run["summary"]["engine_cache"].get("queries") or 0) for run in selected),
            },
            "drain_s": [run["summary"].get("drain_s") for run in selected],
            "engine_waiting_max": max([run["summary"].get("engine_waiting_max") or 0 for run in selected], default=0),
            "actual_window_rate": round(sum(len(run["rows"]) for run in selected) / sum(run["summary"]["window_s"] for run in selected), 4) if selected else None,
            "arrival_delay_ms": {
                "p50": pct([r["arrival_delay_ms"] for run in selected for r in run["rows"]], 0.5),
                "p95": pct([r["arrival_delay_ms"] for run in selected for r in run["rows"]], 0.95),
            },
            "failures": sum(run["summary"].get("failures") or 0 for run in selected),
            "truncated": sum(run["summary"].get("truncated") or 0 for run in selected),
        }
        aggregate[str(limit)]["cache"]["ratio"] = round(
            aggregate[str(limit)]["cache"]["hits"] / aggregate[str(limit)]["cache"]["queries"], 4
        ) if aggregate[str(limit)]["cache"]["queries"] else None

    pairs = []
    for group in (1, 2, 3):
        one = next((r for r in runs if r["group"] == group and r["limit"] == 1), None)
        two = next((r for r in runs if r["group"] == group and r["limit"] == 2), None)
        if not one or not two:
            continue
        pair = {"group": group}
        for scenario in ("short_agent", "cold_rag"):
            a = one["scenario"][scenario]
            b = two["scenario"][scenario]
            pair[scenario] = {
                "limit1": {k: a[k] for k in ("ttft_ms", "gateway_queue_ms", "complete_ms", "per_lane_tps", "requests")},
                "limit2": {k: b[k] for k in ("ttft_ms", "gateway_queue_ms", "complete_ms", "per_lane_tps", "requests")},
                "ttft_p50_delta_pct": round((a["ttft_ms"]["p50"] / b["ttft_ms"]["p50"] - 1) * 100, 2)
                if a["ttft_ms"]["p50"] and b["ttft_ms"]["p50"] else None,
                "gateway_queue_p50_delta_pct": round((a["gateway_queue_ms"]["p50"] / b["gateway_queue_ms"]["p50"] - 1) * 100, 2)
                if a["gateway_queue_ms"]["p50"] and b["gateway_queue_ms"]["p50"] else None,
                "complete_p95_delta_pct": round((a["complete_ms"]["p95"] / b["complete_ms"]["p95"] - 1) * 100, 2)
                if a["complete_ms"]["p95"] and b["complete_ms"]["p95"] else None,
            }
        pair["window_tps"] = {"limit1": one["summary"].get("engine_window_tps"),
                              "limit2": two["summary"].get("engine_window_tps")}
        pairs.append(pair)

    result = {"aggregate": aggregate, "pairs": pairs, "runs": [
        {k: run[k] for k in ("group", "limit", "directory", "scenario")} |
        {"summary": {k: run["summary"].get(k) for k in
                     ("window_requests", "engine_window_tps", "engine_cache", "failures",
                      "truncated", "drain_s", "rate", "warmup_s")}}
        for run in runs
    ]}
    (BASE.parent / "cold-rag-analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"aggregate": aggregate, "pairs": pairs}, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
