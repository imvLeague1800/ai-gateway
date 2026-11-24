"""Print the smoke report tables from saved summaries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE.parents[1] / ".verification" / "dual-qwen" / "smoke"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def concurrency_path(root: Path, scenario: str, concurrency: int) -> Path:
    raw = root / "concurrency" / f"{scenario}-c{concurrency}" / "summary.json"
    return raw if raw.exists() else root / "concurrency" / f"{scenario}-c{concurrency}-summary.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()
    out = args.input
    rows = []
    for scenario in ("agent", "rag_cold", "rag_warm"):
        for c in (1, 2, 3, 4):
            path = concurrency_path(out, scenario, c)
            if not path.exists():
                continue
            d = load(path)
            s = d["sustained"]
            rows.append(
                f"| {scenario} | {c} | {d['samples']} ({d.get('burst_samples', '-')}+{d.get('sustained_requests', '-')}) | "
                f"{d['ttft_ms']['p50']} / {d['ttft_ms']['max']} | "
                f"{d['per_lane_tps']['median']} / {d['per_lane_tps']['p10']} | "
                f"{s['window_output_tps']} | {s['cache']['ratio']} | "
                f"{d['failures']} / {d['truncated']} |"
            )
    print("| 场景 | 并发 | 样本 (burst+sustain) | TTFT P50 / 最慢 (ms) | 每路 TPS 中位 / P10 | 30s 窗口 TPS | 缓存占比 | 失败 / 截断 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    print("\n".join(rows))
    print()
    for scenario in ("agent", "rag_cold", "rag_warm"):
        two = concurrency_path(out, scenario, 2)
        three = concurrency_path(out, scenario, 3)
        if not (two.exists() and three.exists()):
            continue
        a, b = load(two), load(three)
        t2, t3 = a["sustained"]["window_output_tps"], b["sustained"]["window_output_tps"]
        print(f"{scenario}: c2={t2} c3={t3} c3_gain={(t3-t2)/t2:.1%}; "
              f"ttft_p50 {a['ttft_ms']['p50']}->{b['ttft_ms']['p50']}; "
              f"per_lane {a['per_lane_tps']['median']}->{b['per_lane_tps']['median']}")


if __name__ == "__main__":
    main()
