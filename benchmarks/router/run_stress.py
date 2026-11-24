"""Stress entry: full replay, direct concurrency, or long-cold RAG.

No mode stops or starts an engine and no mode uses the benchmark proxy. A real
stop/start drill lives in the separate manual entry `run_fault_drill.py`; this
entry is never called by it. Serialize stress with daily smoke or a drill when
they share the same Spark.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
PYTHON = ROOT / ".verification" / "router-v2" / "litellm-venv" / "Scripts" / "python.exe"
MODES = ("replay", "concurrency", "cold-rag")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", nargs="+", choices=MODES, required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_id = args.run_id or "stress-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = args.output or (ROOT / ".verification" / "dual-qwen" / run_id)
    out.mkdir(parents=True, exist_ok=True)
    stages = []
    total_started = time.perf_counter()
    for mode in args.modes:
        mode_out = out / mode
        if mode == "replay":
            command = ["run_retest.py", "--run-id", f"{run_id}-replay", "--output", str(mode_out)]
        elif mode == "concurrency":
            command = ["run_smoke_concurrency.py", "--run-id", f"{run_id}-concurrency",
                       "--output", str(mode_out)]
        elif mode == "cold-rag":
            command = ["cold_rag/run_cold_rag_matrix.py", "--run-id", f"{run_id}-cold-rag",
                       "--output", str(mode_out)]
        else:
            raise AssertionError(mode)
        print(json.dumps({"START": mode, "command": command}, ensure_ascii=True), flush=True)
        if args.dry_run:
            stages.append({"mode": mode, "command": command, "dry_run": True})
            continue
        log = out / f"{mode}.log"
        started = time.perf_counter()
        with log.open("w", encoding="utf-8") as handle:
            process = subprocess.run([str(PYTHON), *command], cwd=BASE, stdout=handle,
                                     stderr=subprocess.STDOUT, text=True)
        stage = {"mode": mode, "elapsed_s": round(time.perf_counter() - started, 2),
                 "returncode": process.returncode, "log": str(log)}
        stages.append(stage)
        print(json.dumps(stage, ensure_ascii=True), flush=True)
        if process.returncode != 0:
            break
    replay_stage = next((stage for stage in stages if stage.get("mode") == "replay"), None)
    replay_ok = replay_stage is None or replay_stage.get("returncode", 0) == 0
    summary = {
        "run_id": run_id, "modes": args.modes, "stages": stages,
        "replay": {
            "pass": bool(replay_stage is not None and replay_ok),
            "returncode": replay_stage.get("returncode") if replay_stage else None,
            "log": replay_stage.get("log") if replay_stage else None,
            "run_index": str(out / "replay" / "run-index.jsonl"),
        },
        "total_s": round(time.perf_counter() - total_started, 2),
        "pass": all(stage.get("returncode", 0) == 0 for stage in stages) and replay_ok,
        "dry_run": args.dry_run,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    raise SystemExit(0 if summary["pass"] else 1)


if __name__ == "__main__":
    main()
