"""Summarize fault/recovery cycles against the 20-second detection/recovery targets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
DEFAULT_INPUT = ROOT / ".verification" / "dual-qwen" / "fault-v12"
DEFAULT_OUTPUT = ROOT / ".verification" / "dual-qwen" / "analysis" / "fault-analysis.json"


def first_direct_failure(record: dict) -> float | None:
    stop = record.get("stop_started")
    if stop is None:
        return None
    for sample in record.get("monitor_samples", []):
        if sample["at"] >= stop and sample.get("direct_health") != 200:
            return round(sample["at"] - stop, 2)
    return None


def median_or_none(values: list) -> float | None:
    values = [value for value in values if value is not None]
    return median(values) if values else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--host", choices=("all", "15", "16"), default="all")
    args = parser.parse_args()
    out = args.output or (ROOT / ".verification" / "dual-qwen" / args.run_id / "analysis" /
                          "fault-analysis.json" if args.run_id else DEFAULT_OUTPUT)
    if out.suffix != ".json":
        out = out / "fault-analysis.json"
    rows = []
    for path in sorted(args.input.glob("*-qwen*-r*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if args.host != "all" and record.get("host") != args.host:
            continue
        after_removal = record.get("probes_after_removal", [])
        after_recovery = record.get("probes_after_recovery", [])
        target_host = f"10.169.25.{record['host']}"
        survivor_host = "15" if record["host"] == "16" else "16"
        survivor_url = f"http://10.169.25.{survivor_host}:8000"
        inflight = record.get("inflight", [])
        inflight_failures = sum(1 for item in inflight if item.get("state") in
                                {"stream_interrupted", "http_error", "timeout", "stream_timeout"})
        inflight_success = sum(1 for item in inflight if item.get("state") == "success")
        recovered_target = sum(1 for item in after_recovery
                               if item.get("worker") == f"http://{target_host}:8000")
        after_removal_workers = {
            worker: sum(1 for item in after_removal if item.get("worker") == worker)
            for worker in sorted({str(item.get("worker")) for item in after_removal})
        }
        new_evidence = "first_target_success_s" in record
        first_target = record.get("first_target_success_s")
        first_target_ok = (first_target is not None and first_target <= 20) if new_evidence else None
        all_after_removal_on_survivor = (bool(after_removal)
                                         and all(item.get("worker") == survivor_url for item in after_removal))
        all_after_removal_complete = (bool(after_removal)
                                      and all(item.get("complete") for item in after_removal))
        passed = (
            record.get("detection_s") is not None and record["detection_s"] <= 20
            and record.get("router_reaccept_s") is not None and record["router_reaccept_s"] <= 20
            and len(after_removal) == 20 and all(item.get("status") == 200 for item in after_removal)
            and len(after_recovery) == 20 and all(item.get("status") == 200 for item in after_recovery)
            and record.get("running_after", {}).get(record["host"], -1) == 0
            and record.get("running_after", {}).get("15" if record["host"] == "16" else "16", -1) == 0
            and first_target_ok is not False
            and (all_after_removal_on_survivor if new_evidence else True)
            and (all_after_removal_complete if new_evidence else True)
        )
        rows.append({
            "policy": record["policy"], "host": record["host"], "repeat": record["repeat"],
            "elapsed_s": record.get("elapsed_s"),
            "detection_s": record.get("detection_s"),
            "first_direct_failure_s": first_direct_failure(record),
            "stop_command_s": record.get("stop_command_s"),
            "engine_health_s": record.get("engine_health_s"),
            "router_reaccept_s": record.get("router_reaccept_s"),
            "first_target_success_s": first_target,
            "first_target_verified": first_target is not None if new_evidence else None,
            "first_target_probe": record.get("first_target_probe"),
            "preflight": record.get("preflight"),
            "start_error": record.get("start_error"),
            "final_direct_health": record.get("final_direct_health"),
            "before_inflight_running": record.get("before_inflight_running"),
            "inflight_success": inflight_success, "inflight_failures": inflight_failures,
            "after_removal_ok": all(item.get("status") == 200 for item in after_removal),
            "after_removal_workers": after_removal_workers,
            "after_removal_all_on_survivor": all_after_removal_on_survivor,
            "after_removal_all_complete": all_after_removal_complete,
            "after_recovery_ok": all(item.get("status") == 200 for item in after_recovery),
            "after_recovery_workers": {w: sum(1 for item in after_recovery if item.get("worker") == w)
                                        for w in sorted({str(item.get("worker")) for item in after_recovery})},
            "recovered_target_probes": recovered_target,
            "running_after": record.get("running_after"),
            "error": record.get("error"),
            "pass": passed,
        })
    summary = {
        "cycles": len(rows), "passed": sum(1 for row in rows if row["pass"]),
        "hosts": sorted({row["host"] for row in rows}),
        "elapsed_s": {"median": median_or_none([r["elapsed_s"] for r in rows]),
                      "max": max([r["elapsed_s"] for r in rows if r["elapsed_s"] is not None], default=None)},
        "detection_s": {"median": median_or_none([r["detection_s"] for r in rows]),
                        "max": max([r["detection_s"] for r in rows if r["detection_s"] is not None], default=None)},
        "router_reaccept_s": {"median": median_or_none([r["router_reaccept_s"] for r in rows]),
                              "max": max([r["router_reaccept_s"] for r in rows if r["router_reaccept_s"] is not None], default=None)},
        "first_target_success_s": {"median": median_or_none([r["first_target_success_s"] for r in rows]),
                                   "max": max([r["first_target_success_s"] for r in rows if r["first_target_success_s"] is not None], default=None)},
        "engine_health_s": {"median": median_or_none([r["engine_health_s"] for r in rows]),
                            "max": max([r["engine_health_s"] for r in rows if r["engine_health_s"] is not None], default=None)},
        "first_target_verified": sum(1 for r in rows if r["first_target_verified"]),
        "inflight_failures": sum(r["inflight_failures"] for r in rows),
        "all_after_removal_200": all(r["after_removal_ok"] for r in rows),
        "all_after_removal_on_survivor": all(r["after_removal_all_on_survivor"] for r in rows
                                             if r["first_target_verified"] is not None) if any(
            r["first_target_verified"] is not None for r in rows) else None,
        "all_after_removal_complete": all(r["after_removal_all_complete"] for r in rows
                                          if r["first_target_verified"] is not None) if any(
            r["first_target_verified"] is not None for r in rows) else None,
        "all_after_recovery_200": all(r["after_recovery_ok"] for r in rows),
        "all_final_health_200": all(all(v == 200 for v in (r["final_direct_health"] or {}).values())
                                     for r in rows) if any(r["final_direct_health"] for r in rows) else None,
    }
    result = {"summary": summary, "rows": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
