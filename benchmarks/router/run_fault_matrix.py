"""Run the 12 fault/recovery cycles: 2 policies x 2 replicas x 3 repeats."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
OUT = ROOT / ".verification" / "dual-qwen" / "fault-v12"
PYTHON = str(ROOT / ".verification" / "router-v2" / "litellm-venv" / "Scripts" / "python.exe")
COMBOS = [
    ("cache_aware", "15", 1),
    ("power_of_two", "15", 1), ("cache_aware", "16", 1),
    ("power_of_two", "16", 2), ("cache_aware", "15", 2),
    ("power_of_two", "15", 2), ("cache_aware", "16", 2),
    ("power_of_two", "16", 3), ("cache_aware", "15", 3),
    ("power_of_two", "15", 3), ("cache_aware", "16", 3),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = OUT / "index.jsonl"
    index.write_text("", encoding="utf-8")
    for policy, host, repeat in COMBOS:
        log = OUT / f"{policy}-qwen{host}-r{repeat}.log"
        started = time.time()
        print(f"START {policy} qwen{host} r{repeat}", flush=True)
        with log.open("w", encoding="utf-8") as fh:
            process = subprocess.run(
                [PYTHON, "fault_retest.py", "--policy", policy, "--host", host, "--repeat", str(repeat)],
                cwd=BASE, stdout=fh, stderr=subprocess.STDOUT, text=True,
            )
        record = {"policy": policy, "host": host, "repeat": repeat, "returncode": process.returncode,
                  "elapsed_s": round(time.time() - started, 2), "log": str(log)}
        result_path = OUT / f"{policy}-qwen{host}-r{repeat}.json"
        if result_path.exists():
            record["result"] = str(result_path)
        with index.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        print(json.dumps(record, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
