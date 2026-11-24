"""Aggregate smoke core, queue, direct concurrency and failure evidence."""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
OUT = ROOT / ".verification" / "dual-qwen" / "smoke"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    result = {"core": {}, "queue": {}, "concurrency": {}, "failure": None}
    for policy in ("power_of_two", "cache_aware"):
        path = OUT / policy / "summary.json"
        if path.exists():
            data = read(path)
            result["core"][policy] = {
                key: data.get(key) for key in
                ("requests", "states", "protocol_ok", "quality_ok", "groups", "warm",
                 "workers", "both_workers_routed", "cache", "recall", "policy_ok", "namespace")
            }
        queue_path = OUT / f"queue-{policy}" / "queue-summary.json"
        if queue_path.exists():
            data = read(queue_path)
            result["queue"][policy] = {
                key: data.get(key) for key in
                ("queue_before_cancel", "queue_after_waiting_cancel", "queue_after_generating_cancel",
                 "cancelled_ids", "completed", "states", "recovery", "cancel_observation")
            }

    for scenario in ("agent", "rag_cold", "rag_warm"):
        values = {}
        for concurrency in (1, 2, 3, 4):
            path = OUT / "concurrency" / f"{scenario}-c{concurrency}" / "summary.json"
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

    failure_path = OUT / "failure-summary.json"
    if failure_path.exists():
        result["failure"] = read(failure_path)

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
    (BASE / "smoke-analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"core": {k: v["policy_ok"] for k, v in result["core"].items()},
                      "concurrency": acceptance,
                      "failure": None if result["failure"] is None else result["failure"]["all_200"]},
                     indent=2))


if __name__ == "__main__":
    main()
