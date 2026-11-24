"""Daily five-to-ten-minute smoke: candidate replay and queue/cancel, no fault."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
PYTHON = ROOT / ".verification" / "router-v2" / "litellm-venv" / "Scripts" / "python.exe"
POLICIES = ("power_of_two", "cache_aware")
ENGINES = {"15": "http://10.169.25.15:8000/health", "16": "http://10.169.25.16:8000/health"}


def restart_router(policy: str) -> None:
    import run_retest
    run_retest.restart(policy)


def run_step(name: str, command: list[str], log: Path) -> dict:
    started = time.perf_counter()
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.run([str(PYTHON), *command], cwd=BASE, stdout=handle,
                                 stderr=subprocess.STDOUT, text=True)
    return {"name": name, "elapsed_s": round(time.perf_counter() - started, 2),
            "returncode": process.returncode, "log": str(log)}


def read_stage_summary(path: Path) -> dict:
    if not path.exists():
        return {"summary_error": f"missing {path.name}"}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"summary_error": f"{type(exc).__name__}: {exc}"}
    return loaded if isinstance(loaded, dict) else {"summary_error": "summary is not an object"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=POLICIES, default="cache_aware")
    parser.add_argument("--baseline", action="store_true", help="also run the other policy on the same 28 requests")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    run_id = args.run_id or "smoke-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = ROOT / ".verification" / "dual-qwen" / run_id
    if out.exists():
        print(json.dumps({"run_id": run_id, "pass": False,
                          "error": "run ID directory already exists; choose a new --run-id "
                                   "so a previous pass=true summary cannot be reused"},
                         indent=2))
        raise SystemExit(2)
    out.mkdir(parents=True)
    stages = []
    total_started = time.perf_counter()
    try:
        if not args.no_restart:
            started = time.perf_counter()
            restart_router(args.policy)
            stages.append({"name": "router_ready", "elapsed_s": round(time.perf_counter() - started, 2)})
            (out / "router-ready.json").write_text(json.dumps({"policy": args.policy}), encoding="utf-8")

        core = run_step("core", ["smoke_replay.py", "--policy", args.policy, "--output", str(out / args.policy)],
                        out / f"core-{args.policy}.log")
        stages.append(core)
        core_summary = read_stage_summary(out / args.policy / "summary.json")

        queue = run_step("queue", ["smoke_queue.py", "--policy", args.policy, "--output", str(out / f"queue-{args.policy}")],
                         out / f"queue-{args.policy}.log")
        stages.append(queue)
        queue_summary = read_stage_summary(out / f"queue-{args.policy}" / "queue-summary.json")

        baseline_summary = None
        if args.baseline:
            baseline = next(p for p in POLICIES if p != args.policy)
            if args.no_restart:
                raise RuntimeError("--baseline requires Router restart")
            started = time.perf_counter()
            restart_router(baseline)
            stages.append({"name": "baseline_router_ready", "elapsed_s": round(time.perf_counter() - started, 2)})
            baseline_step = run_step("baseline_core",
                                     ["smoke_replay.py", "--policy", baseline, "--output", str(out / baseline)],
                                     out / f"core-{baseline}.log")
            stages.append(baseline_step)
            baseline_summary = read_stage_summary(out / baseline / "summary.json")
    finally:
        if args.baseline and not args.no_restart:
            started = time.perf_counter()
            restart_router(args.policy)
            stages.append({"name": "restore_candidate_router", "elapsed_s": round(time.perf_counter() - started, 2)})

    analysis = run_step("analysis", ["analyze_smoke.py", "--input", str(out), "--output",
                                     str(out / "analysis" / "smoke-analysis.json")],
                        out / "analysis.log")
    stages.append(analysis)
    total_s = round(time.perf_counter() - total_started, 2)
    final_health = {}
    for name, url in ENGINES.items():
        try:
            final_health[name] = httpx.get(url, timeout=5, trust_env=False).status_code
        except Exception:
            final_health[name] = 0
    summary = {
        "run_id": run_id, "policy": args.policy, "baseline": args.baseline,
        "stages": stages, "total_s": total_s, "within_10_minutes": total_s <= 600,
        "both_engines_running_after": all(v == 200 for v in final_health.values()),
        "final_health": final_health,
        "core": {k: core_summary.get(k) for k in
                 ("requests", "states", "protocol_ok", "quality_ok", "policy_ok", "cache", "recall",
                  "both_workers_routed")},
        "queue": {k: queue_summary.get(k) for k in
                  ("states", "cancelled_ids", "completed", "recovery")},
        "router": {
            "policy": args.policy,
            "started_by_smoke": not args.no_restart,
            "health_check_interval_secs": 5,
            "health_failure_threshold": 2,
            "health_success_threshold": 2,
        },
        "baseline_core": {k: baseline_summary.get(k) for k in
                          ("requests", "states", "protocol_ok", "quality_ok", "policy_ok", "cache",
                           "recall", "both_workers_routed")} if baseline_summary else None,
        "pass": bool(core.get("returncode") == 0
                     and queue.get("returncode") == 0
                     and core_summary.get("policy_ok")
                     and queue_summary.get("recovery", {}).get("recovered")
                     and not queue_summary.get("recovery", {}).get("router_running", 1)
                     and analysis.get("returncode") == 0
                     and all(v == 200 for v in final_health.values())),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary["pass"] else 1)


if __name__ == "__main__":
    main()
