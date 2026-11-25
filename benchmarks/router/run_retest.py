"""Run the frozen 3-group paired retest through the isolated Router."""
from __future__ import annotations

import argparse
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
EXPECTED_ROUNDS = len(GROUPS) * len(PROFILES) * 2


def read_result(log_path: Path) -> tuple[dict | None, str]:
    """Return (payload, reason); reason is empty only for a usable result."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return None, f"log unreadable: {type(exc).__name__}: {exc}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None, "log is empty"
    try:
        payload = json.loads(lines[-1])
    except Exception as exc:
        return None, f"last log line is not JSON: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, "result is not a JSON object"
    directory = payload.get("directory")
    if not directory:
        return None, "result has no directory"
    if not Path(directory).is_dir():
        return None, f"result directory does not exist: {directory}"
    requests = payload.get("requests")
    if not isinstance(requests, int) or requests <= 0:
        return None, f"result has no positive request count: {requests!r}"
    return payload, ""


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    out = args.output or (OUT if not args.run_id else
                          ROOT / ".verification" / "dual-qwen" / args.run_id / "replay")
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "run-index.jsonl"
    index_path.write_text("", encoding="utf-8")
    failures: list[dict] = []
    for group in GROUPS:
        order = ("power_of_two", "cache_aware") if group % 2 else ("cache_aware", "power_of_two")
        for profile in PROFILES:
            for policy in order:
                restart(policy)
                namespace = f"retest-v12-g{group}-{profile}-{uuid.uuid4().hex[:10]}"
                log_path = out / f"g{group}-{profile}-{policy}.log"
                command = [PYTHON, "replay.py", profile, "--policy", policy, "--group", str(group),
                           "--namespace", namespace, "--budgets", "budgets.json", "--output", str(out)]
                started = time.time()
                print(f"START {profile} group={group} policy={policy} namespace={namespace}", flush=True)
                with log_path.open("w", encoding="utf-8") as log:
                    process = subprocess.run(command, cwd=BASE, stdout=log, stderr=subprocess.STDOUT, text=True)
                record = {"profile": profile, "group": group, "policy": policy, "namespace": namespace,
                          "returncode": process.returncode, "log": str(log_path), "started": started,
                          "elapsed_s": round(time.time() - started, 2)}
                base_failure = {key: record[key] for key in
                                ("group", "profile", "policy", "returncode", "log")}
                if process.returncode != 0:
                    record["result_ok"] = False
                    failures.append({**base_failure,
                                     "reason": f"child returned {process.returncode}"})
                else:
                    payload, result_error = read_result(log_path)
                    if result_error:
                        record["result_ok"] = False
                        record["result_error"] = result_error
                        failures.append({**base_failure,
                                         "reason": f"invalid result: {result_error}"})
                    else:
                        directory = Path(payload.pop("directory"))
                        record.update(directory=str(directory), summary=payload,
                                      result_ok=True)
                with index_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=True) + "\n")
                print(json.dumps({k: record.get(k) for k in ("profile", "group", "policy", "returncode", "elapsed_s")}, ensure_ascii=True), flush=True)
    if failures:
        print(json.dumps({"pass": False, "failures": failures,
                          "run_index": str(index_path)}, ensure_ascii=True), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
