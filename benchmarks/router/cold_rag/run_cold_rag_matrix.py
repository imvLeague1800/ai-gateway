"""Run the three paired long-cold groups against the first-version Scheduler."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
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


def port_in_use(port: int = 18080) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_ready(process: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"scheduler exited before ready (returncode {process.returncode})")
        try:
            if httpx.get(URL + "/health/ready", timeout=2, trust_env=False).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("scheduler did not become ready")


def start_scheduler(limit: int, log_path: Path, config_path: Path):
    if port_in_use():
        log_path.write_text("scheduler port 18080 already in use\n", encoding="utf-8")
        raise RuntimeError("scheduler port 18080 already in use")
    env = os.environ.copy()
    env.update({
        "BACKENDS_CONFIG": str(config_path),
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
        wait_ready(process)
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    out = args.output or (OUT if not args.run_id else
                          ROOT / ".verification" / "dual-qwen" / args.run_id / "cold-rag")
    if out.exists():
        print(f"output directory already exists: {out}", flush=True)
        raise SystemExit(2)
    out.mkdir(parents=True)
    index = out / "run-index.jsonl"
    index.write_text("", encoding="utf-8")
    order = ((1, 2), (2, 1), (1, 2))
    for group, limits in enumerate(order, start=1):
        for limit in limits:
            run_dir = out / f"g{group}-long{limit}"
            run_dir.mkdir(parents=True, exist_ok=True)
            config_path = run_dir / "backends.yaml"
            config_path.write_text(
                (BASE / "backends-template.yaml").read_text(encoding="utf-8").replace(
                    "__MAX_LONG_REQUESTS__", str(limit)
                ), encoding="utf-8"
            )
            sched_log = out / f"g{group}-long{limit}-scheduler.log"
            client_log = out / f"g{group}-long{limit}-client.log"
            started = time.time()
            process = None
            log = None
            record = {
                "group": group, "limit": limit, "rate": 0.23, "duration_s": 120,
                "warmup_s": 15, "returncode": 1, "elapsed_s": 0.0,
                "directory": str(run_dir), "scheduler_log": str(sched_log),
                "client_log": str(client_log), "config": str(config_path),
                "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            }
            try:
                process, log = start_scheduler(limit, sched_log, config_path)
                with client_log.open("w", encoding="utf-8") as client:
                    result = subprocess.run(
                        [str(PYTHON), str(BASE / "cold_rag_replay.py"),
                         "--limit", str(limit), "--group", str(group), "--rate", "0.23",
                         "--duration", "120", "--warmup", "15",
                         "--output", str(run_dir), "--config", str(config_path),
                         "--tag", f"matrix-g{group}-l{limit}"],
                        cwd=BASE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        stdout=client, stderr=subprocess.STDOUT, text=True,
                    )
                record["returncode"] = result.returncode
                summary_path = run_dir / "summary.json"
                if summary_path.exists():
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    record["summary"] = {k: summary.get(k) for k in
                                         ("window_requests", "by_scenario", "engine_window_tps",
                                          "engine_cache", "failures", "truncated", "drain_s")}
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                record["elapsed_s"] = round(time.time() - started, 2)
                if process is not None:
                    stop_scheduler(process, log)
                with index.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")
                print(json.dumps(record, ensure_ascii=True), flush=True)
            if record["returncode"] != 0:
                sys.exit(record["returncode"])


if __name__ == "__main__":
    main()
