"""Manual real-fault drill: stop and recover the `.16` Qwen service.

This entry is never called by daily smoke, stress, CI or a timer. It requires an
explicit `--maintenance-window-ack`, stops only the `.16` Qwen Compose service,
keeps `.15` and the gateway untouched, and always attempts to start `.16` again
after a stop was issued. Single-cycle and multi-cycle runs reuse one ready
isolated Router and health-check configuration; the Router is not stopped
during a drill. `--preflight-only` runs the read-only checks and exits without
starting, removing or stopping anything.

Preflight is read-only: SSH login, the `/home/dgx/spark-vllm` Compose project and
its `qwen` service (Compose labels and running state), both direct engine
health, the `.16` model, and the isolated Router readiness with both direct
workers. It runs no Compose dry-run and makes no authorization inference; the
`dgx@10.169.25.16` account is already proven for these stop/start commands. The
Router is started only after the read-only checks pass, and only when no
matching Router is already ready. Recovery timing uses one monotonic clock: the
first direct `/health` 200 timestamp saved by the health poll (even if the start
command has not returned yet) and the end of the first complete `.16` response
through the isolated Router. The gap must be 20.00 s or less.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

import transport

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
SSH_HOST = "dgx@10.169.25.16"
REMOTE_DIR = "/home/dgx/spark-vllm"
QWEN_SERVICE = "qwen"
QWEN_CONTAINER = "spark-vllm-qwen36"
ROUTER_NAME = "vllm-router-test"
REMOTE_START = "/tmp/router-benchmark-start-router.sh"
TARGET_HOST = "10.169.25.16"
TARGET_URL = "http://10.169.25.16:8000"
SURVIVOR_URL = "http://10.169.25.15:8000"
ROUTER_API = "http://127.0.0.1:18100"
ROUTER_METRICS = "http://127.0.0.1:19100/metrics"
STOP_COMMAND = f"cd {REMOTE_DIR} && docker compose --profile {QWEN_SERVICE} stop {QWEN_SERVICE}"
START_COMMAND = f"cd {REMOTE_DIR} && docker compose --profile {QWEN_SERVICE} start {QWEN_SERVICE}"
QUERY_COMMAND = f"cd {REMOTE_DIR} && docker compose --profile {QWEN_SERVICE} ps -q {QWEN_SERVICE}"
SERVICE_FORMAT = (
    "{{.State.Status}}|"
    '{{index .Config.Labels "com.docker.compose.project.working_dir"}}|'
    '{{index .Config.Labels "com.docker.compose.service"}}'
)


class PreflightError(RuntimeError):
    pass


def utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")


def ssh(command: str, *, check: bool = True, timeout: int = 120) -> dict:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", SSH_HOST, command],
            check=False, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ssh timeout after {timeout}s: {command}") from exc
    payload = {
        "host": SSH_HOST,
        "command": command,
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "elapsed_s": round(time.perf_counter() - started, 2),
    }
    if check and result.returncode != 0:
        raise RuntimeError(f"ssh command failed ({result.returncode}): {payload['stderr']}")
    return payload


def target_service() -> dict:
    """Read-only check of the `.16` Compose project and Qwen service identity."""
    ps = ssh(QUERY_COMMAND, check=False, timeout=30)
    ids = [line.strip() for line in (ps.get("stdout") or "").splitlines() if line.strip()]
    result: dict = {"query": ps, "container_ids": ids, "ok": False, "reason": ""}
    if ps.get("returncode") != 0:
        result["reason"] = f"service query failed ({ps.get('returncode')}): {ps.get('stderr')}"
        return result
    if len(ids) != 1:
        result["reason"] = f"expected exactly one qwen container; got {len(ids)}"
        return result
    inspect = ssh(f"docker inspect --format '{SERVICE_FORMAT}' {ids[0]}",
                  check=False, timeout=15)
    result["inspect"] = inspect
    fields = (inspect.get("stdout") or "").strip().split("|")
    if inspect.get("returncode") != 0 or len(fields) != 3:
        result["reason"] = (f"service inspect failed ({inspect.get('returncode')}): "
                            f"{inspect.get('stderr') or inspect.get('stdout')}")
        return result
    status, working_dir, service = fields
    result.update(status=status, working_dir=working_dir, service=service)
    if service != QWEN_SERVICE:
        result["reason"] = f"service label is {service!r}, expected {QWEN_SERVICE!r}"
    elif working_dir != REMOTE_DIR:
        result["reason"] = (f"project working dir is {working_dir!r}, "
                            f"expected {REMOTE_DIR!r}")
    elif status != "running":
        result["reason"] = f"service state is {status!r}, expected running"
    else:
        result["ok"] = True
    return result


def target_models() -> list[str]:
    response = httpx.get(TARGET_URL + "/v1/models", timeout=5, trust_env=False)
    response.raise_for_status()
    return [row.get("id") for row in response.json().get("data", [])]


def copy_start_script() -> None:
    content = (BASE / "start-router.sh").read_text(encoding="utf-8").replace("\r\n", "\n").encode()
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", SSH_HOST, f"cat > {REMOTE_START}"],
        input=content, check=False, capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("could not copy start-router.sh to .16: "
                           + result.stderr.decode("utf-8", errors="replace").strip())
    ssh(f"chmod +x {REMOTE_START}", timeout=15)


def router_ready() -> bool:
    try:
        models = httpx.get(ROUTER_API + "/v1/models", timeout=3, trust_env=False)
    except Exception:
        return False
    return (models.status_code == 200
            and transport.router_worker_health("10.169.25.15:8000") == 1
            and transport.router_worker_health("10.169.25.16:8000") == 1)


def wait_router_ready(timeout: float = 90) -> float | None:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if router_ready():
            return round(time.perf_counter() - started, 2)
        time.sleep(0.5)
    return None


def start_router(policy: str) -> float:
    copy_start_script()
    ssh(f"{REMOTE_START} {policy}", timeout=120)
    elapsed = wait_router_ready()
    if elapsed is None:
        raise RuntimeError("isolated Router did not become ready with both direct workers")
    return elapsed


def router_container_id() -> str:
    output = ssh(f"docker ps -q -f name={ROUTER_NAME}", check=False, timeout=15)["stdout"]
    return output.splitlines()[0] if output else ""


def router_state() -> dict:
    """Read-only check of an already running isolated Router and its policy."""
    if not router_ready():
        return {"ready": False, "container_id": "", "policy": None, "args": []}
    container_id = router_container_id()
    inspect = ssh(f"docker inspect --format '{{{{json .Args}}}}' {container_id}",
                  check=False, timeout=15)
    arguments: list = []
    try:
        arguments = json.loads(inspect.get("stdout") or "[]")
    except ValueError:
        arguments = []
    policy = None
    if isinstance(arguments, list) and "--policy" in arguments:
        index = arguments.index("--policy")
        if index + 1 < len(arguments):
            policy = arguments[index + 1]
    return {"ready": True, "container_id": container_id, "policy": policy,
            "args": arguments}


def remove_router() -> dict:
    return ssh(f"docker rm -f {ROUTER_NAME}", check=False, timeout=30)


def service_state() -> dict:
    ps = ssh(f"cd {REMOTE_DIR} && docker compose --profile {QWEN_SERVICE} ps -q {QWEN_SERVICE}",
             check=False, timeout=30)
    inspect = ssh(f"docker inspect --format '{{{{.State.Status}}}}' {QWEN_CONTAINER}",
                  check=False, timeout=15)
    status = inspect["stdout"].strip()
    query_ok = bool(ps["returncode"] == 0 and (ps["stdout"] or "").strip()
                    and inspect["returncode"] == 0 and status)
    return {"ps": ps, "inspect": inspect, "status": status,
            "running": status == "running", "query_ok": query_ok}


def router_running_by_worker() -> dict[str, float]:
    try:
        text = httpx.get(ROUTER_METRICS, timeout=3, trust_env=False).text
    except Exception:
        return {}
    values = transport.parse_metrics(text, {"vllm_router_running_requests"})
    return {json.loads(labels).get("worker", "").rstrip("/"): value
            for labels, value in values["vllm_router_running_requests"].items()}


async def wait_running_workers(count: int, timeout: float = 10) -> dict[str, float] | None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        values = router_running_by_worker()
        if sum(1 for value in values.values() if value >= 1) >= count:
            return values
        await asyncio.sleep(0.05)
    return None


async def wait_worker_idle(worker: str, timeout: float = 10) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if router_running_by_worker().get(worker.rstrip("/"), 0) == 0:
            return True
        await asyncio.sleep(0.05)
    return False


def long_spec(index: int, prefix: str = "drill-load") -> dict:
    return {
        "id": f"{prefix}-{index}", "kind": "qa",
        "messages": [{"role": "user", "content":
                      f"{uuid.uuid4().hex} Write the numbers from 1 to 900 separated by spaces."}],
        "tools": None, "quality": ".", "quality_type": "regex", "budget_ms": 180000,
        "max_tokens": 1024,
    }


async def cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def probe_rows(client: httpx.AsyncClient, start: int, count: int, origin: float,
                     deadline: float | None = None) -> list[dict]:
    rows = []
    for index in range(start, start + count):
        remaining = None if deadline is None else deadline - time.perf_counter()
        if remaining is not None and remaining <= 0:
            break
        try:
            row = await asyncio.wait_for(transport.send_short_probe(client, index),
                                         timeout=remaining)
        except asyncio.TimeoutError:
            break
        row["after_origin_s"] = round(row["at"] - origin, 2)
        rows.append(row)
    return rows


def on_worker(row: dict, url: str) -> bool:
    return str(row.get("worker") or "").rstrip("/") == url.rstrip("/")


def first_target_probe(rows: list[dict], target_url: str) -> dict | None:
    return next((row for row in rows if on_worker(row, target_url) and row.get("complete")), None)


def worker_counts(rows: list[dict]) -> dict:
    return {worker: sum(1 for row in rows if row.get("worker") == worker)
            for worker in sorted({str(row.get("worker")) for row in rows})}


async def steer_probe(client: httpx.AsyncClient, start: int, count: int,
                      origin: float) -> tuple[list[dict], dict | None, dict]:
    """Keep `.15` busy and cancel the `.16` steering request so probes use `.16`."""
    loads: list[asyncio.Task] = []
    try:
        load1 = asyncio.create_task(transport.send_one(client, long_spec(start + 1)))
        loads.append(load1)
        running = await wait_running_workers(1)
        if running is None:
            raise RuntimeError("steering load did not start on any worker")
        load1_worker = next(worker for worker, value in running.items() if value >= 1)

        load2 = asyncio.create_task(transport.send_one(client, long_spec(start + 2)))
        loads.append(load2)
        running = await wait_running_workers(2)
        if running is None:
            raise RuntimeError("steering loads did not occupy both workers")
        load2_worker = next(worker for worker, value in running.items()
                            if value >= 1 and worker != load1_worker)

        target_load = load1 if load1_worker == TARGET_URL.rstrip("/") else load2
        keeper = load2 if target_load is load1 else load1
        steering = {
            "load1_worker": load1_worker,
            "load2_worker": load2_worker,
            "cancelled_worker": TARGET_URL,
            "keeper_worker": SURVIVOR_URL,
        }
        await cancel_task(target_load)
        if not await wait_worker_idle(TARGET_URL):
            raise RuntimeError("the .16 steering load did not release the worker")
        if keeper.done():
            raise RuntimeError("the .15 steering load ended before probes")
        rows = await probe_rows(client, start, count, origin)
        return rows, first_target_probe(rows, TARGET_URL), steering
    finally:
        for task in loads:
            if not task.done():
                await cancel_task(task)
        await wait_worker_idle(SURVIVOR_URL)
        await wait_worker_idle(TARGET_URL)
        await asyncio.sleep(0.2)


async def wait_direct_state(host: str, healthy: bool, timeout: float) -> float | None:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        code = await transport.direct_engine_health(host)
        if (code == 200) == healthy:
            return round(time.perf_counter() - started, 2)
        await asyncio.sleep(0.25)
    return None


async def wait_direct_health_timestamp(host: str, timeout: float) -> dict | None:
    """Save the monotonic instant of the first direct `/health` 200 itself.

    The timestamp is taken inside the poll, so a start command that returns after
    the engine is already healthy does not shift the recovery measurement.
    """
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        code = await transport.direct_engine_health(host)
        if code == 200:
            seen = time.perf_counter()
            return {"perf_counter": seen, "utc": utc_text(time.time())}
        await asyncio.sleep(0.25)
    return None


async def wait_worker_health(expected: float, timeout: float,
                             origin: float | None = None) -> float | None:
    origin = time.perf_counter() if origin is None else origin
    while time.perf_counter() - origin < timeout:
        if transport.router_worker_health("10.169.25.16:8000") == expected:
            return round(time.perf_counter() - origin, 2)
        await asyncio.sleep(0.25)
    return None


async def health_now() -> dict[str, int]:
    return {"15": await transport.direct_engine_health("10.169.25.15"),
            "16": await transport.direct_engine_health("10.169.25.16")}


async def preflight(args) -> dict:
    """Read-only checks; no stop, start, Router rebuild or Engine load."""
    try:
        login = ssh("echo preflight-ok", timeout=15)
        account = ssh("id -un", timeout=15)
    except Exception as exc:
        raise PreflightError(
            f"SSH login to {SSH_HOST} failed: {type(exc).__name__}: {exc}") from exc
    account_name = (account.get("stdout") or "").strip()
    if not account_name:
        raise PreflightError("cannot determine the executing account on .16")
    service = target_service()
    if not service["ok"]:
        error = PreflightError(f".16 target service check failed: {service['reason']}")
        error.evidence = {"service": {key: service.get(key) for key in
                                      ("container_ids", "status", "working_dir",
                                       "service", "reason")}}
        raise error
    before = await health_now()
    if before != {"15": 200, "16": 200}:
        raise PreflightError(
            f"both direct engines must be healthy before the drill: {before}")
    try:
        model_ids = target_models()
    except Exception as exc:
        raise PreflightError(
            f"cannot read .16 models: {type(exc).__name__}: {exc}") from exc
    if transport.MODEL not in model_ids:
        raise PreflightError(f".16 is not serving {transport.MODEL}: {model_ids}")
    router = router_state()
    return {
        "ssh_login": login,
        "account": account,
        "direct_health": before,
        "model_ids": model_ids,
        "target_service": {key: service.get(key) for key in
                           ("container_ids", "status", "working_dir", "service", "ok")},
        "router": router,
    }


async def collect_inflight(tasks: list[asyncio.Task], timeout: float = 90,
                           deadline: float | None = None) -> list[dict]:
    rows = []
    for task in tasks:
        remaining = timeout if deadline is None else deadline - time.perf_counter()
        if remaining <= 0:
            await cancel_task(task)
            rows.append({"status": None, "state": "evidence_timeout",
                         "error": "evidence collection limit reached"})
            continue
        try:
            row = await asyncio.wait_for(task, timeout=min(timeout, remaining))
            rows.append({k: row.get(k) for k in
                         ("status", "state", "error", "finish_reason", "done",
                          "ttft_ms", "complete_ms")})
        except asyncio.TimeoutError:
            await cancel_task(task)
            rows.append({"status": None, "state": "evidence_timeout",
                         "error": "evidence collection limit reached"})
        except asyncio.CancelledError:
            rows.append({"status": None, "state": "cancelled", "error": "inflight cancelled"})
        except Exception as exc:
            rows.append({"status": None, "state": "exception",
                         "error": f"{type(exc).__name__}: {exc}"})
    return rows


def perf_to_utc(anchor: dict, perf_counter: float) -> str:
    return utc_text(anchor["unix"] + (perf_counter - anchor["perf_counter"]))


async def recover_cycle(client: httpx.AsyncClient, record: dict, args) -> None:
    start_started = time.perf_counter()
    record["start_cmd_perf_counter"] = start_started
    record["start_cmd_utc"] = utc_text(time.time())
    try:
        health_task = asyncio.create_task(
            wait_direct_health_timestamp(TARGET_HOST, args.health_timeout))
        record["start"] = await asyncio.to_thread(
            ssh, START_COMMAND, check=False, timeout=180)
        start_returned = time.perf_counter()
        record["start_ok"] = record["start"]["returncode"] == 0
        record["start_returned_s"] = round(start_returned - start_started, 3)
        health = await health_task
        if health is None:
            record["recovery_error"] = (
                f".16 engine health did not recover within {args.health_timeout}s")
            record["final_health"] = await health_now()
            record["recovery_confirmed"] = False
            record["router_accepts_16"] = False
            return
        record["first_direct_health"] = health
        record["start_to_health_s"] = round(health["perf_counter"] - start_started, 3)
        record["health_before_start_returned"] = bool(
            health["perf_counter"] < start_returned)
        record["service_after_start"] = service_state()
        record["service_running"] = bool(record["service_after_start"]["running"])
        if not record["start_ok"]:
            record["recovery_error"] = (
                f"start command failed ({record['start']['returncode']}): "
                f"{record['start']['stderr']}")
        if not record["service_running"]:
            record.setdefault("recovery_error", "Qwen service is not running after start")
        record["router_reaccept_s"] = await wait_worker_health(
            1, args.reaccept_timeout, health["perf_counter"])
        if record["router_reaccept_s"] is None:
            record.setdefault("recovery_error",
                              f"Router did not reaccept .16 within {args.reaccept_timeout}s")
        else:
            rows, target, steering = await steer_probe(
                client, 500 + record["cycle"] * 100, args.probe_count,
                health["perf_counter"])
            record["recovery"] = {
                "workers": worker_counts(rows),
                "first_target_success_s": target["after_origin_s"] if target else None,
                "first_target_probe": target,
                "load_steering": steering,
                "rows": rows,
            }
            record["recovery_first_target_s"] = (target["after_origin_s"]
                                                 if target else None)
            record["recovery_request_id"] = target.get("request_id") if target else None
            ended = (target or {}).get("ended")
            gap = None if ended is None else ended - health["perf_counter"]
            record["first_complete_response"] = {
                "request_id": (target or {}).get("request_id"),
                "worker": (target or {}).get("worker"),
                "complete": bool((target or {}).get("complete")),
                "done": (target or {}).get("done"),
                "finish_reason": (target or {}).get("finish_reason"),
                "ended_perf_counter": ended,
                "ended_utc": perf_to_utc(record["clock_anchor"], ended) if ended is not None else None,
            }
            record["recovery_gap_s"] = gap
            record["health_to_accept_s"] = round(gap, 3) if gap is not None else None
            if not (record["first_complete_response"]["complete"]
                    and record["first_complete_response"]["request_id"]):
                record.setdefault("recovery_error",
                                  "first recovery response was not complete or had no request id")
        record["final_health"] = await health_now()
        record["recovery_confirmed"] = bool(
            record["start_ok"] and record.get("service_running")
            and record.get("first_direct_health") is not None
            and record["final_health"] == {"15": 200, "16": 200})
        record["router_accepts_16"] = bool(
            record.get("router_reaccept_s") is not None
            and record.get("recovery_gap_s") is not None
            and record["recovery_gap_s"] <= args.reaccept_timeout
            and record.get("first_complete_response", {}).get("complete")
            and on_worker(record.get("first_complete_response") or {}, TARGET_URL))
    except Exception as exc:
        record["recovery_error"] = f"{type(exc).__name__}: {exc}"
        record["recovery_confirmed"] = False
        record["router_accepts_16"] = False
        try:
            record["final_health"] = await health_now()
        except Exception:
            record["final_health"] = {}


def cycle_ok(record: dict, args) -> bool:
    first = record.get("first_complete_response") or {}
    removal = record.get("removal") or {}
    inflight = (record.get("inflight") or {}).get("rows") or []
    gap = record.get("recovery_gap_s")
    return bool(
        not record.get("error")
        and (record.get("pre_stop_health") or {}).get("15") == 200
        and (record.get("pre_stop_health") or {}).get("16") == 200
        and record.get("pre_stop_target_complete")
        and (record.get("stop") or {}).get("returncode") == 0
        and record.get("service_stopped")
        and record.get("direct_failure_s") is not None
        and record.get("detection_s") is not None
        and record["detection_s"] <= args.detection_timeout
        and removal.get("all_on_15")
        and removal.get("all_complete")
        and len(inflight) == 4
        and record.get("start_ok")
        and record.get("service_running")
        and (record.get("first_direct_health") or {}).get("perf_counter") is not None
        and gap is not None
        and gap <= args.reaccept_timeout
        and first.get("complete")
        and on_worker(first, TARGET_URL)
        and first.get("request_id")
        and record.get("router_reaccept_s") is not None
        and (record.get("final_health") or {}).get("15") == 200
        and (record.get("final_health") or {}).get("16") == 200
        and record.get("recovery_confirmed")
        and record.get("router_accepts_16")
        and record.get("same_router_container")
        and record.get("evidence_complete")
    )


async def run_cycle(client: httpx.AsyncClient, args, index: int,
                    context: dict) -> dict:
    record: dict = {
        "cycle": index,
        "policy": args.policy,
        "router_container_id_before": context["container_id"],
        "detection_target_s": args.detection_timeout,
        "reaccept_target_s": args.reaccept_timeout,
        "evidence_timeout_s": args.evidence_timeout,
        "clock_anchor": {"perf_counter": time.perf_counter(), "unix": time.time()},
        "inflight": {"before_running": {}, "rows": []},
    }
    inflight_tasks: list[asyncio.Task] = []
    evidence_deadline: float | None = None
    stop_issued = False
    try:
        record["pre_stop_health"] = await health_now()
        if record["pre_stop_health"] != {"15": 200, "16": 200}:
            raise RuntimeError(f"both direct engines must be healthy: {record['pre_stop_health']}")

        origin = time.perf_counter()
        rows, target, steering = await steer_probe(client, 100 + index * 100, args.probe_count, origin)
        record["pre_stop"] = {
            "workers": worker_counts(rows),
            "first_target_success_s": target["after_origin_s"] if target else None,
            "first_target_probe": target,
            "load_steering": steering,
            "rows": rows,
        }
        record["pre_stop_target_complete"] = bool(
            target and target.get("complete") and on_worker(target, TARGET_URL))
        if not record["pre_stop_target_complete"]:
            raise RuntimeError("pre-stop phase had no complete Router response from .16")

        inflight_tasks = [asyncio.create_task(
            transport.send_one(client, long_spec(index * 10 + i, "drill-inflight")))
            for i in range(4)]
        await asyncio.sleep(0.5)
        record["inflight"]["before_running"] = router_running_by_worker()

        stop_issued = True
        stop_started = time.perf_counter()
        evidence_deadline = stop_started + args.evidence_timeout
        record["evidence_deadline"] = {"stop_started_perf_counter": stop_started,
                                       "limit_s": args.evidence_timeout}
        record["stop_started_utc"] = utc_text(time.time())
        failure_task = asyncio.create_task(wait_direct_state(TARGET_HOST, False, 30))
        detection_task = asyncio.create_task(
            wait_worker_health(0, args.detection_timeout, stop_started))
        record["stop"] = await asyncio.to_thread(
            ssh, STOP_COMMAND, check=False, timeout=180)
        record["service_after_stop"] = service_state()
        record["service_stopped"] = bool(
            record["service_after_stop"]["ps"]["returncode"] == 0
            and not record["service_after_stop"]["ps"]["stdout"].strip()
            and record["service_after_stop"]["inspect"]["stdout"].strip()
            in ("exited", "created", "dead"))
        if not record["service_stopped"]:
            record.setdefault("error", "Qwen service state after stop was not confirmed")
        record["direct_failure_s"] = await failure_task
        record["detection_s"] = await detection_task
        record["stop_command_s"] = record["stop"]["elapsed_s"]
        if record["direct_failure_s"] is None:
            record.setdefault("error", "direct .16 health did not fail after stop")
        if record["detection_s"] is None:
            record.setdefault("error",
                              f"Router did not remove .16 within {args.detection_timeout}s")

        removed = await probe_rows(client, 300 + index * 100, args.probe_count,
                                   stop_started, deadline=evidence_deadline)
        removal_complete = len(removed) == args.probe_count
        record["removal"] = {
            "workers": worker_counts(removed),
            "attempted": len(removed),
            "timed_out": not removal_complete,
            "all_on_15": all(on_worker(row, SURVIVOR_URL) for row in removed),
            "all_complete": all(row.get("complete") for row in removed),
            "rows": removed,
        }
        if not removal_complete:
            record.setdefault("error", "removal probes hit the evidence time limit")
        elif not (record["removal"]["all_on_15"] and record["removal"]["all_complete"]):
            record.setdefault("error", "removal probes were not all complete on .15")
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if inflight_tasks:
            record["inflight"]["rows"] = await collect_inflight(
                inflight_tasks, deadline=evidence_deadline)
        removal = record.get("removal") or {}
        inflight_rows = record["inflight"]["rows"]
        record["evidence_complete"] = bool(
            removal.get("attempted") == args.probe_count
            and removal.get("all_on_15") and removal.get("all_complete")
            and len(inflight_rows) == 4
            and not any(row.get("state") == "evidence_timeout" for row in inflight_rows))
        if not record["evidence_complete"]:
            record.setdefault("error", "post-removal evidence incomplete within the time limit")
        if stop_issued:
            await recover_cycle(client, record, args)
        else:
            record["final_health"] = await health_now()
        record["router_container_id_after"] = router_container_id()
        record["same_router_container"] = bool(
            record["router_container_id_before"]
            and record["router_container_id_before"] == record["router_container_id_after"])
        record["pass"] = cycle_ok(record, args)
    return record


def compact_cycle(record: dict) -> dict:
    keys = ("cycle", "pass", "service_stopped", "direct_failure_s", "detection_s",
            "stop_command_s", "evidence_timeout_s", "evidence_complete",
            "start_to_health_s", "health_before_start_returned",
            "health_to_accept_s", "recovery_gap_s", "service_running",
            "router_reaccept_s", "recovery_first_target_s", "recovery_request_id",
            "recovery_confirmed", "router_accepts_16", "same_router_container",
            "error", "recovery_error")
    return {key: record.get(key) for key in keys}


async def async_main(args) -> int:
    run_id = args.run_id or "fault-drill-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = ROOT / ".verification" / "dual-qwen" / run_id
    if out.exists():
        print(json.dumps({"run_id": run_id, "pass": False,
                          "error": "run ID directory already exists; choose a new --run-id"},
                         indent=2))
        return 2
    out.mkdir(parents=True)
    started = time.perf_counter()
    summary: dict = {
        "run_id": run_id,
        "mode": "manual-real-fault-drill",
        "policy": args.policy,
        "cycles_requested": args.cycles,
        "cycles_completed": 0,
        "maintenance_window_ack": args.maintenance_window_ack,
        "preflight_only": args.preflight_only,
        "target": ".16",
        "survivor": ".15",
        "actual_engines_stopped": [],
        "cycles": [],
        "pass": False,
    }
    exit_code = 1
    router_started = False
    try:
        if not args.maintenance_window_ack and not args.preflight_only:
            raise PreflightError(
                "maintenance window not acknowledged; pass --maintenance-window-ack "
                "only after confirming SSH access and the maintenance window")
        summary["preflight"] = await preflight(args)
        router = summary["preflight"]["router"]
        if args.preflight_only:
            if not router["ready"]:
                raise PreflightError(
                    "isolated Router is not ready with both direct Qwen workers; "
                    "--preflight-only does not start it")
            if router.get("policy") and router["policy"] != args.policy:
                raise PreflightError(
                    f"isolated Router policy is {router['policy']!r}, but this run "
                    f"requests {args.policy!r}")
            summary["pass"] = True
            exit_code = 0
            return exit_code
        if router["ready"] and router.get("policy") == args.policy:
            ready = wait_router_ready(10)
            if ready is None:
                raise PreflightError(
                    "the ready isolated Router stopped accepting both direct Qwen workers")
        elif args.no_restart:
            raise PreflightError(
                "no matching isolated Router is ready; --no-restart forbids starting one")
        else:
            router_started = True
            ready = start_router(args.policy)
        summary["router"] = {
            "policy": args.policy,
            "started_by_drill": router_started,
            "reused": not router_started,
            "ready_s": ready,
            "container_id": router_container_id(),
            "worker_urls": ["http://10.169.25.15:8000", "http://10.169.25.16:8000"],
            "health_check_interval_secs": 5,
            "health_failure_threshold": 2,
            "health_success_threshold": 2,
        }
        if not summary["router"]["container_id"]:
            raise PreflightError("isolated Router container not found")
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10, read=180, write=60, pool=20),
                trust_env=False) as client:
            for index in range(1, args.cycles + 1):
                cycle = await run_cycle(client, args, index, summary["router"])
                (out / f"cycle-{index:02d}.json").write_text(
                    json.dumps(cycle, indent=2, ensure_ascii=True), encoding="utf-8")
                summary["cycles"].append(compact_cycle(cycle))
                summary["cycles_completed"] = index
                if cycle.get("service_stopped"):
                    summary["actual_engines_stopped"].append(".16")
                if not (cycle.get("recovery_confirmed") and cycle.get("router_accepts_16")):
                    summary["aborted_reason"] = (
                        f"cycle {index} did not confirm .16 recovery/acceptance; "
                        "no further cycles started")
                    break
        summary["pass"] = bool(
            summary["cycles_completed"] == args.cycles
            and len(summary["cycles"]) == args.cycles
            and all(row.get("pass") for row in summary["cycles"])
            and not summary.get("aborted_reason"))
        exit_code = 0 if summary["pass"] else 1
    except PreflightError as exc:
        summary["preflight_error"] = f"{type(exc).__name__}: {exc}"
        evidence = getattr(exc, "evidence", None)
        if evidence:
            summary["preflight_evidence"] = evidence
        exit_code = 2
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            summary["final_health"] = await health_now()
        except Exception as exc:
            summary["final_health"] = {}
            summary["final_health_error"] = f"{type(exc).__name__}: {exc}"
        if router_started and not args.keep_router:
            try:
                summary["router_removed"] = remove_router()
            except Exception as exc:
                summary["router_remove_error"] = f"{type(exc).__name__}: {exc}"
        summary["total_s"] = round(time.perf_counter() - started, 2)
        (out / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=("cache_aware", "power_of_two"),
                        default="cache_aware")
    parser.add_argument("--cycles", type=int, default=1,
                        help="number of complete stop/recover cycles")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--probe-count", type=int, default=20)
    parser.add_argument("--detection-timeout", type=float, default=20,
                        help="target seconds from the stop command to Router removal")
    parser.add_argument("--reaccept-timeout", type=float, default=20,
                        help="target seconds from the first direct /health 200 to the end "
                             "of the first complete .16 response through the Router")
    parser.add_argument("--evidence-timeout", type=float, default=60,
                        help="combined seconds for post-removal probes and in-flight evidence; "
                             "on timeout the cycle is marked incomplete and recovery starts")
    parser.add_argument("--health-timeout", type=float, default=900,
                        help="engine weight-load timeout after start")
    parser.add_argument("--no-restart", action="store_true",
                        help="require an already ready isolated Router; do not start one")
    parser.add_argument("--keep-router", action="store_true")
    parser.add_argument("--preflight-only", action="store_true",
                        help="run the read-only checks and exit; never starts or removes "
                             "the isolated Router and never stops or starts Qwen")
    parser.add_argument("--maintenance-window-ack", action="store_true",
                        help="acknowledge the maintenance window; required for any run "
                             "that may stop the .16 Qwen service")
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
