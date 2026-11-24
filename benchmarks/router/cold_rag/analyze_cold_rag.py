"""Aggregate paired cold-RAG admission runs with the shared scenario statistics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scenario_stats as stats

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
DEFAULT_INPUT = ROOT / ".verification" / "dual-qwen" / "cold-rag" / "matrix"
DEFAULT_OUTPUT = ROOT / ".verification" / "dual-qwen" / "analysis" / "cold-rag-analysis.json"


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--directory-prefix", default="",
                        help="rewrite run directory labels for archive reconstruction")
    args = parser.parse_args()
    out = args.output or (ROOT / ".verification" / "dual-qwen" / args.run_id / "analysis" /
                          "cold-rag-analysis.json" if args.run_id else DEFAULT_OUTPUT)
    if out.suffix != ".json":
        out = out / "cold-rag-analysis.json"
    runs = []
    for path in sorted(args.input.glob("g*-long*")):
        summary_path = path / "summary.json"
        requests_path = path / "requests.jsonl"
        if not (summary_path.exists() and requests_path.exists()):
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = read_jsonl(requests_path)
        window, outside, unassignable = stats.assign_window(
            rows, summary["window_start"], summary["window_end"]
        )
        runs.append({
            "group": summary["group"], "limit": summary["limit"],
            "directory": str(Path(args.directory_prefix) / path.name) if args.directory_prefix else str(path),
            "summary": summary,
            "rows": window, "all_rows": rows,
            "outside": outside, "unassignable": unassignable,
            "scenario": stats.summarize_scenarios(window),
        })
    aggregate = {}
    for limit in (1, 2):
        selected = [r for r in runs if r["limit"] == limit]
        rows = [row for run in selected for row in run["rows"]]
        scenario = stats.summarize_scenarios(rows)
        aggregate[str(limit)] = {
            "runs": len(selected),
            "groups": [run["group"] for run in selected],
            "scenario": scenario["by_scenario"],
            "unknown_scenario": scenario["unknown_scenario"],
            "unassignable_requests": sum(len(run["unassignable"]) for run in selected),
            "outside_requests": sum(len(run["outside"]) for run in selected),
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
            "actual_window_rate": round(sum(len(run["rows"]) for run in selected) /
                                        sum(run["summary"]["window_s"] for run in selected), 4) if selected else None,
            "arrival_delay_ms": {
                "p50": stats._pct([r["arrival_delay_ms"] for run in selected for r in run["rows"]
                                   if r.get("arrival_delay_ms") is not None], 0.5),
                "p95": stats._pct([r["arrival_delay_ms"] for run in selected for r in run["rows"]
                                   if r.get("arrival_delay_ms") is not None], 0.95),
            },
            "failures": scenario["failures"],
            "truncated": scenario["truncated"],
            "states": scenario["states"],
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
        for scenario in stats.SCENARIOS:
            a = one["scenario"]["by_scenario"][scenario]
            b = two["scenario"]["by_scenario"][scenario]
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
                      "truncated", "drain_s", "rate", "warmup_s")},
         "unassignable_requests": len(run["unassignable"]),
         "outside_requests": len(run["outside"])}
        for run in runs
    ]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"output": str(out), "aggregate": aggregate, "pairs": pairs},
                     indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
