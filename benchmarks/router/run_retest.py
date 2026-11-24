"""Run the frozen 3-group paired retest through the isolated Router."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
OUT = ROOT / ".verification" / "dual-qwen" / "retest-v12"
PYTHON = str(ROOT / ".verification" / "router-v2" / "litellm-venv" / "Scripts" / "python.exe")
ROUTER_API = "http://127.0.0.1:18100"
ROUTER_METRICS = "http://127.0.0.1:19100/metrics"
GROUPS = (1, 2, 3)
PROFILES = ("stable", "high", "light")


def restart(policy: str) -> None:
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "dgx@10.169.25.16",
         f"/home/dgx/spark-vllm/reports/dual-qwen/run-router.sh {policy}"],
        check=True, capture_output=True, text=True, timeout=120,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            models = httpx.get(ROUTER_API + "/v1/models", timeout=3, trust_env=False)
            metrics = httpx.get(ROUTER_METRICS, timeout=3, trust_env=False).text
            healthy = sum(1 for line in metrics.splitlines() if line.startswith("vllm_router_worker_health") and line.endswith(" 1"))
            if models.status_code == 200 and healthy == 2:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"router {policy} did not become ready")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index_path = OUT / "run-index.jsonl"
    index_path.write_text("", encoding="utf-8")
    for group in GROUPS:
        order = ("power_of_two", "cache_aware") if group % 2 else ("cache_aware", "power_of_two")
        for profile in PROFILES:
            for policy in order:
                restart(policy)
                namespace = f"retest-v12-g{group}-{profile}-{uuid.uuid4().hex[:10]}"
                log_path = OUT / f"g{group}-{profile}-{policy}.log"
                command = [PYTHON, "replay.py", profile, "--policy", policy, "--group", str(group),
                           "--namespace", namespace, "--budgets", "budgets.json", "--output", str(OUT)]
                started = time.time()
                print(f"START {profile} group={group} policy={policy} namespace={namespace}", flush=True)
                with log_path.open("w", encoding="utf-8") as log:
                    process = subprocess.run(command, cwd=BASE, stdout=log, stderr=subprocess.STDOUT, text=True)
                record = {"profile": profile, "group": group, "policy": policy, "namespace": namespace,
                          "returncode": process.returncode, "log": str(log_path), "started": started,
                          "elapsed_s": round(time.time() - started, 2)}
                if process.returncode == 0:
                    try:
                        payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
                        directory = Path(payload.pop("directory"))
                        record.update(directory=str(directory), summary=payload)
                    except Exception as exc:
                        record["parse_error"] = f"{type(exc).__name__}: {exc}"
                with index_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=True) + "\n")
                print(json.dumps({k: record.get(k) for k in ("profile", "group", "policy", "returncode", "elapsed_s")}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
