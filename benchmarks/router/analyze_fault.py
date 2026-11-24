"""Summarize the 12 fault/recovery cycles against the 20-second targets."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "fault-v12"


def first_direct_failure(record: dict) -> float | None:
    stop = record.get("stop_started")
    if stop is None:
        return None
    for sample in record.get("monitor_samples", []):
        if sample["at"] >= stop and sample.get("direct_health") != 200:
            return round(sample["at"] - stop, 2)
    return None


def main() -> None:
    rows = []
    for path in sorted(OUT.glob("*-qwen*-r*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        after_removal = record.get("probes_after_removal", [])
        after_recovery = record.get("probes_after_recovery", [])
        target_host = f"10.169.25.{record['host']}"
        inflight = record.get("inflight", [])
        inflight_failures = sum(1 for item in inflight if item.get("state") in {"stream_interrupted", "http_error", "timeout", "stream_timeout"})
        inflight_success = sum(1 for item in inflight if item.get("state") == "success")
        recovered_target = sum(1 for item in after_recovery if item.get("worker") == f"http://{target_host}:8000")
        passed = (
            record.get("detection_s") is not None and record["detection_s"] <= 20
            and record.get("router_reaccept_s") is not None and record["router_reaccept_s"] <= 20
            and len(after_removal) == 20 and all(item.get("status") == 200 for item in after_removal)
            and len(after_recovery) == 20 and all(item.get("status") == 200 for item in after_recovery)
            and record.get("running_after", {}).get(record["host"], -1) == 0
            and record.get("running_after", {}).get("15" if record["host"] == "16" else "16", -1) == 0
        )
        rows.append({
            "policy": record["policy"], "host": record["host"], "repeat": record["repeat"],
            "detection_s": record.get("detection_s"), "first_direct_failure_s": first_direct_failure(record),
            "stop_command_s": record.get("stop_command_s"),
            "engine_health_s": record.get("engine_health_s"), "router_reaccept_s": record.get("router_reaccept_s"),
            "before_inflight_running": record.get("before_inflight_running"),
            "inflight_success": inflight_success, "inflight_failures": inflight_failures,
            "after_removal_ok": all(item.get("status") == 200 for item in after_removal),
            "after_recovery_ok": all(item.get("status") == 200 for item in after_recovery),
            "after_recovery_workers": {w: sum(1 for item in after_recovery if item.get("worker") == w)
                                        for w in sorted({str(item.get("worker")) for item in after_recovery})},
            "recovered_target_probes": recovered_target,
            "running_after": record.get("running_after"),
            "pass": passed,
        })
    summary = {
        "cycles": len(rows), "passed": sum(1 for row in rows if row["pass"]),
        "detection_s": {"median": median([r["detection_s"] for r in rows if r["detection_s"] is not None]),
                        "max": max([r["detection_s"] for r in rows if r["detection_s"] is not None], default=None)},
        "router_reaccept_s": {"median": median([r["router_reaccept_s"] for r in rows if r["router_reaccept_s"] is not None]),
                              "max": max([r["router_reaccept_s"] for r in rows if r["router_reaccept_s"] is not None], default=None)},
        "engine_health_s": {"median": median([r["engine_health_s"] for r in rows if r["engine_health_s"] is not None]),
                            "max": max([r["engine_health_s"] for r in rows if r["engine_health_s"] is not None], default=None)},
        "inflight_failures": sum(r["inflight_failures"] for r in rows),
        "all_after_removal_200": all(r["after_removal_ok"] for r in rows),
        "all_after_recovery_200": all(r["after_recovery_ok"] for r in rows),
    }
    result = {"summary": summary, "rows": rows}
    (BASE / "fault-analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
