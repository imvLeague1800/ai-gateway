"""Run the direct single-replica concurrency matrix: 3 scenarios x 1-4 lanes."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
OUT = ROOT / ".verification" / "dual-qwen" / "smoke" / "concurrency"
PYTHON = str(ROOT / ".verification" / "router-v2" / "litellm-venv" / "Scripts" / "python.exe")
SCENARIOS = ("agent", "rag_cold", "rag_warm")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = OUT / "run-index.jsonl"
    index.write_text("", encoding="utf-8")
    for scenario in SCENARIOS:
        for concurrency in (1, 2, 3, 4):
            directory = OUT / f"{scenario}-c{concurrency}"
            log = OUT / f"{scenario}-c{concurrency}.log"
            started = time.time()
            print(f"START {scenario} c={concurrency}", flush=True)
            with log.open("w", encoding="utf-8") as fh:
                process = subprocess.run(
                    [PYTHON, "smoke_concurrency.py", "--scenario", scenario,
                     "--concurrency", str(concurrency), "--output", str(directory)],
                    cwd=BASE, stdout=fh, stderr=subprocess.STDOUT, text=True,
                )
            record = {"scenario": scenario, "concurrency": concurrency,
                      "returncode": process.returncode, "elapsed_s": round(time.time() - started, 2),
                      "directory": str(directory), "log": str(log)}
            if (directory / "summary.json").exists():
                summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
                record["summary"] = {k: summary.get(k) for k in
                                     ("samples", "ttft_ms", "per_lane_tps", "sustained",
                                      "failures", "truncated", "quality_ok", "protocol_ok")}
            with index.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
            print(json.dumps(record, ensure_ascii=True), flush=True)
            if process.returncode != 0:
                sys.exit(process.returncode)


if __name__ == "__main__":
    main()
