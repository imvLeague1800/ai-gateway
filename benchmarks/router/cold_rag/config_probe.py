"""Small isolated probes for the final two-replica Qwen gateway config."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
sys.path.insert(0, str(BASE.parent))
import cold_rag_replay as cold
import transport

PYTHON = ROOT / "scheduler" / ".venv" / "Scripts" / "python.exe"
SCHEDULER_DIR = ROOT / "scheduler"
KEY = "cold-rag-test-key"
OUT = ROOT / ".verification" / "dual-qwen" / "cold-rag" / "config-probe"
ROOT_CONFIG = ROOT / "config" / "backends.yaml"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_ready(port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=2,
                         trust_env=False).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"scheduler {port} not ready")


def start(config: Path, port: int, log: Path):
    env = os.environ.copy()
    env.update({
        "BACKENDS_CONFIG": str(config),
        "SCHEDULER_INTERNAL_API_KEY": KEY,
        "MAX_ACTIVE_PER_PRINCIPAL": "1",
        "MAX_QUEUED_PER_PRINCIPAL": "2",
        "QUEUE_TIMEOUT_SECONDS": "300",
        "REQUEST_TIMEOUT_SECONDS": "600",
        "HEALTH_INTERVAL_SECONDS": "5",
        "MAX_QUEUE_DEPTH": "50",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    handle = log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--no-access-log"],
        cwd=SCHEDULER_DIR, env=env, stdout=handle, stderr=subprocess.STDOUT, text=True,
    )
    try:
        wait_ready(port)
    except Exception:
        process.terminate()
        process.wait(15)
        handle.close()
        raise
    return process, handle


def stop(process, handle) -> None:
    process.terminate()
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(10)
    handle.close()


async def short_request(port: int, user: str) -> dict:
    body = {
        "model": "qwen3.8-27b",
        "messages": [{"role": "user", "content": "Reply only OK"}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
        "user": user,
    }
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.post(f"http://127.0.0.1:{port}/v1/chat/completions",
                                     headers={"Authorization": f"Bearer {KEY}"}, json=body)
    return {
        "status": response.status_code,
        "backend_id": response.headers.get("x-gateway-backend-id"),
        "body_model": (response.json().get("model") if response.headers.get("content-type", "").startswith("application/json") else None),
        "body_error": (response.json().get("error") if response.headers.get("content-type", "").startswith("application/json") else None),
    }


async def long_request(port: int, index: int) -> dict:
    spec = cold.cold_rag_case(f"PROBE-{index}", f"PROBE-CODE-{index}")
    spec.update({"user": f"probe-user-{index}", "max_tokens": 512, "budget_ms": 30000})
    headers = {}
    async with httpx.AsyncClient(timeout=180, trust_env=False,
                                 headers={"Authorization": f"Bearer {KEY}"}) as client:
        result = await transport.send_one(
            client, spec, router_api=f"http://127.0.0.1:{port}",
            on_response_headers=lambda values: headers.update(values),
        )
    queue = headers.get("x-gateway-queue-wait-seconds")
    return {
        "status": result["status"], "state": result["state"], "ttft_ms": result["ttft_ms"],
        "backend_id": headers.get("x-gateway-backend-id"),
        "queue_wait_ms": round(float(queue) * 1000, 2) if queue not in (None, "") else None,
        "output_tokens": (result.get("usage") or {}).get("completion_tokens"),
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"actual_replicas": {}, "root_config": {}, "single_replica": {}, "context_rejection": {}}
    for host in ("10.169.25.15", "10.169.25.16"):
        try:
            response = httpx.get(f"http://{host}:8000/v1/models", timeout=10, trust_env=False)
            data = response.json()["data"][0]
            result["actual_replicas"][host] = {"status": response.status_code,
                                               "model": data["id"], "max_model_len": data.get("max_model_len")}
        except Exception as exc:
            result["actual_replicas"][host] = {"error": type(exc).__name__ + ": " + str(exc)}

    process, handle = start(ROOT_CONFIG, 18081, OUT / "root-scheduler.log")
    try:
        result["root_config"]["requests"] = await asyncio.gather(
            short_request(18081, "root-probe-0"), short_request(18081, "root-probe-1"),
            short_request(18081, "root-probe-2"),
        )
        result["root_config"]["sha256"] = sha(ROOT_CONFIG)
    finally:
        stop(process, handle)

    template = (BASE / "backends-template.yaml").read_text(encoding="utf-8")
    single = OUT / "single-backends.yaml"
    single.write_text(template.replace("__MAX_LONG_REQUESTS__", "2"), encoding="utf-8")
    process, handle = start(single, 18082, OUT / "single-scheduler.log")
    try:
        result["single_replica"] = {
            "config": str(single), "config_sha256": sha(single),
            "long_requests": await asyncio.gather(*(long_request(18082, i) for i in range(3))),
        }
    finally:
        stop(process, handle)

    context_config = OUT / "context-reject-backends.yaml"
    context_config.write_text(
        (single.read_text(encoding="utf-8")
         .replace("max_output_tokens: 1024", "max_output_tokens: 262143")
         .replace("default_output_tokens: 1024", "default_output_tokens: 1024")),
        encoding="utf-8",
    )
    process, handle = start(context_config, 18083, OUT / "context-scheduler.log")
    try:
        body = {"model": "qwen3.6-35b-a3b-nvfp4", "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 262143, "temperature": 0, "stream": False, "user": "context-probe"}
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            response = await client.post(
                "http://127.0.0.1:18083/v1/chat/completions",
                headers={"Authorization": f"Bearer {KEY}"}, json=body,
            )
        result["context_rejection"] = {"status": response.status_code,
                                       "body": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:500],
                                       "config_sha256": sha(context_config)}
    finally:
        stop(process, handle)

    result["litellm_alias"] = (ROOT / "config" / "litellm.yaml").read_text(encoding="utf-8").splitlines()[:8]
    (OUT / "config-probe.json").write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
