"""Run the three paired long-cold groups against the first-version Scheduler."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
SCHEDULER_DIR = ROOT / "scheduler"
PYTHON = SCHEDULER_DIR / ".venv" / "Scripts" / "python.exe"
OUT = ROOT / ".verification" / "dual-qwen" / "cold-rag" / "matrix"
URL = "http://127.0.0.1:18080"
KEY = "cold-rag-test-key"


def wait_ready(timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(URL + "/health/ready", timeout=2, trust_env=False).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("scheduler did not become ready")


def start_scheduler(limit: int, log_path: Path):
    env = os.environ.copy()
    env.update({
        "BACKENDS_CONFIG": str(BASE / f"backends-long{limit}.yaml"),
        "SCHEDULER_INTERNAL_API_KEY": KEY,
        "MAX_ACTIVE_PER_PRINCIPAL": "1",
        "MAX_QUEUED_PER_PRINCIPAL": "2",
        "QUEUE_TIMEOUT_SECONDS": "300",
        "REQUEST_TIMEOUT_SECONDS": "600",
        "HEALTH_INTERVAL_SECONDS": "5",
        "MAX_QUEUE_DEPTH": "50",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", "18080", "--no-access-log"],
        cwd=SCHEDULER_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_ready()
    except Exception:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        raise
    return process, log


def stop_scheduler(process: subprocess.Popen, log) -> None:
    process.terminate()
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(10)
    log.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = OUT / "run-index.jsonl"
    index.write_text("", encoding="utf-8")
    order = ((1, 2), (2, 1), (1, 2))
    for group, limits in enumerate(order, start=1):
        for limit in limits:
            run_dir = OUT / f"g{group}-long{limit}"
            sched_log = OUT / f"g{group}-long{limit}-scheduler.log"
            client_log = OUT / f"g{group}-long{limit}-client.log"
            started = time.time()
            process = None
            log = None
            try:
                process, log = start_scheduler(limit, sched_log)
                with client_log.open("w", encoding="utf-8") as client:
                    result = subprocess.run(
                        [str(PYTHON), str(BASE / "cold_rag_replay.py"),
                         "--limit", str(limit), "--group", str(group), "--rate", "0.23",
                         "--duration", "120", "--warmup", "15",
                         "--output", str(run_dir), "--tag", f"matrix-g{group}-l{limit}"],
                        cwd=BASE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        stdout=client, stderr=subprocess.STDOUT, text=True,
                    )
                record = {
                    "group": group, "limit": limit, "rate": 0.23, "duration_s": 120,
                    "warmup_s": 15, "returncode": result.returncode,
                    "elapsed_s": round(time.time() - started, 2),
                    "directory": str(run_dir), "scheduler_log": str(sched_log),
                    "client_log": str(client_log),
                }
                summary_path = run_dir / "summary.json"
                if summary_path.exists():
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    record["summary"] = {k: summary.get(k) for k in
                                         ("window_requests", "by_scenario", "engine_window_tps",
                                          "engine_cache", "failures", "truncated", "drain_s")}
                with index.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")
                print(json.dumps(record, ensure_ascii=True), flush=True)
                if result.returncode != 0:
                    sys.exit(result.returncode)
            finally:
                if process is not None:
                    stop_scheduler(process, log)


if __name__ == "__main__":
    main()
