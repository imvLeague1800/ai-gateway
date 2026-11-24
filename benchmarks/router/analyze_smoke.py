"""Aggregate daily smoke core/queue and optional historical failover/concurrency evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
DEFAULT_INPUT = ROOT / ".verification" / "dual-qwen" / "smoke"
DEFAULT_OUTPUT = ROOT / ".verification" / "dual-qwen" / "analysis" / "smoke-analysis.json"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def core_path(root: Path, policy: str) -> Path:
    raw = root / policy / "summary.json"
    return raw if raw.exists() else root / "summaries" / f"{policy}-summary.json"


def queue_path(root: Path, policy: str) -> Path:
    raw = root / f"queue-{policy}" / "queue-summary.json"
    return raw if raw.exists() else root / "summaries" / f"queue-{policy}.json"


def concurrency_path(root: Path, scenario: str, concurrency: int) -> Path:
    raw = root / "concurrency" / f"{scenario}-c{concurrency}" / "summary.json"
    return raw if raw.exists() else root / "concurrency" / f"{scenario}-c{concurrency}-summary.json"


def failover_path(root: Path) -> Path:
    raw = root / "failover" / "summary.json"
    return raw if raw.exists() else root / "summaries" / "failover-summary.json"


def failure_path(root: Path) -> Path:
    raw = root / "failure-summary.json"
    return raw if raw.exists() else root / "summaries" / "failure-summary.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    out = args.output or (ROOT / ".verification" / "dual-qwen" / args.run_id / "analysis" /
                          "smoke-analysis.json" if args.run_id else DEFAULT_OUTPUT)
    if out.suffix != ".json":
        out = out / "smoke-analysis.json"
    result = {"core": {}, "queue": {}, "concurrency": {}, "failure": None}
    for policy in ("power_of_two", "cache_aware"):
        path = core_path(args.input, policy)
        if path.exists():
            data = read(path)
            result["core"][policy] = {
                key: data.get(key) for key in
                ("requests", "states", "protocol_ok", "quality_ok", "groups", "warm",
                 "workers", "both_workers_routed", "cache", "recall", "policy_ok", "namespace")
            }
        queue = queue_path(args.input, policy)
        if queue.exists():
            data = read(queue)
            result["queue"][policy] = {
                key: data.get(key) for key in
                ("queue_before_cancel", "queue_after_waiting_cancel", "queue_after_generating_cancel",
                 "cancelled_ids", "completed", "states", "recovery", "cancel_observation")
            }

    for scenario in ("agent", "rag_cold", "rag_warm"):
        values = {}
        for concurrency in (1, 2, 3, 4):
            path = concurrency_path(args.input, scenario, concurrency)
            if not path.exists():
                continue
            data = read(path)
            values[str(concurrency)] = {
                "samples": data["samples"],
                "ttft_ms": data["ttft_ms"],
                "per_lane_tps": data["per_lane_tps"],
                "output_tokens": data["output_tokens"],
                "sustained": data["sustained"],
                "burst_cache": data.get("burst_cache"),
                "states": data["states"],
                "failures": data["failures"],
                "truncated": data["truncated"],
                "quality_ok": data["quality_ok"],
                "protocol_ok": data["protocol_ok"],
            }
        result["concurrency"][scenario] = values

    failover = failover_path(args.input)
    if failover.exists():
        result["failover"] = read(failover)
    failure = failure_path(args.input)
    if failure.exists():
        result["failure"] = read(failure)

    acceptance = {}
    for scenario, values in result["concurrency"].items():
        two = values.get("2", {}).get("sustained", {}).get("window_output_tps")
        three = values.get("3", {}).get("sustained", {}).get("window_output_tps")
        if two and three:
            gain = (three - two) / two
            acceptance[scenario] = {
                "c2_tps": two, "c3_tps": three, "c3_gain_vs_c2": round(gain, 4),
                "c3_gain_at_least_10pct": gain >= 0.10,
                "c3_ttft_p50": values["3"]["ttft_ms"]["p50"],
                "c2_ttft_p50": values["2"]["ttft_ms"]["p50"],
                "c3_per_lane_tps_median": values["3"]["per_lane_tps"]["median"],
                "c2_per_lane_tps_median": values["2"]["per_lane_tps"]["median"],
            }
    result["acceptance"] = acceptance
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"input": str(args.input), "output": str(out),
                      "core": {k: v["policy_ok"] for k, v in result["core"].items()},
                      "concurrency": acceptance,
                      "failover": None if result.get("failover") is None else result["failover"].get("pass"),
                      "failure": None if result["failure"] is None else result["failure"]["all_200"]},
                     indent=2))


if __name__ == "__main__":
    main()
