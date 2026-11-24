"""Print markdown tables from a cold-rag analysis archive."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_ANALYSIS = BASE.parent / "runs" / "cold-rag-v1" / "analysis" / "cold-rag-analysis.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, default=DEFAULT_ANALYSIS)
    args = parser.parse_args()
    data = json.loads(args.analysis.read_text(encoding="utf-8"))
    print("| 长冷上限 | 场景 | 请求数 | TTFT P50/P95 | 每路 TPS P50/P10 | 网关排队 P50/P95 | 引擎排队 | 完成 P95 | 稳态整机 TPS | 失败/超时/截断 |")
    print("| ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |")
    for limit in ("1", "2"):
        for scenario, label in (("short_agent", "短 Agent"), ("cold_rag", "冷文档")):
            v = data["aggregate"][limit]["scenario"][scenario]
            print(f"| {limit} | {label} | {v['requests']} | {v['ttft_ms']['p50']} / {v['ttft_ms']['p95']} | "
                  f"{v['per_lane_tps']['p50']} / {v['per_lane_tps']['p10']} | "
                  f"{v['gateway_queue_ms']['p50']} / {v['gateway_queue_ms']['p95']} | "
                  f"未验证 (waiting max {data['aggregate'][limit]['engine_waiting_max']}) | "
                  f"{v['complete_ms']['p95']} | {data['aggregate'][limit]['engine_window_tps']} | "
                  f"{data['aggregate'][limit]['failures']} / {data['aggregate'][limit]['truncated']} |")
    print()
    for pair in data["pairs"]:
        a = pair["short_agent"]
        b = pair["cold_rag"]
        print(f"| {pair['group']} | {pair['window_tps']['limit1']} / {pair['window_tps']['limit2']} | "
              f"{a['limit1']['ttft_ms']['p50']} / {a['limit1']['ttft_ms']['p95']} vs {a['limit2']['ttft_ms']['p50']} / {a['limit2']['ttft_ms']['p95']} | "
              f"{b['limit1']['ttft_ms']['p50']} / {b['limit1']['ttft_ms']['p95']} vs {b['limit2']['ttft_ms']['p50']} / {b['limit2']['ttft_ms']['p95']} | "
              f"{b['limit1']['complete_ms']['p95']} / {b['limit2']['complete_ms']['p95']} |")


if __name__ == "__main__":
    main()
