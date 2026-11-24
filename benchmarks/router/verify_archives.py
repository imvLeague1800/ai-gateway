"""Offline verification that the versioned archives can rebuild the analyses."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
RUNS = BASE / "runs"
DEFAULT_OUTPUT = ROOT / ".verification" / "dual-qwen" / "archive-verification.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> None:
    subprocess.run([sys.executable, *command], cwd=BASE, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = {"archives": {}, "rebuild": {}, "removed_root_copies": {}, "pass": True}
    with tempfile.TemporaryDirectory(prefix="router-archive-check-") as temp:
        temp = Path(temp)

        smoke = RUNS / "smoke-v1" / "analysis" / "smoke-analysis.json"
        smoke_out = temp / "smoke-analysis.json"
        run(["analyze_smoke.py", "--input", str(RUNS / "smoke-v1"), "--output", str(smoke_out)])
        result["rebuild"]["smoke"] = {"path": str(smoke), "sha256": sha(smoke),
                                      "rebuilt_sha256": sha(smoke_out),
                                      "equal": smoke.read_bytes() == smoke_out.read_bytes()}

        retest = RUNS / "retest-v12" / "analysis" / "retest-analysis.json"
        retest_out = temp / "retest-analysis.json"
        run(["analyze_retest.py", "--input", str(RUNS / "retest-v12"), "--output", str(retest_out)])
        result["rebuild"]["retest"] = {"path": str(retest), "sha256": sha(retest),
                                       "rebuilt_sha256": sha(retest_out),
                                       "equal": retest.read_bytes() == retest_out.read_bytes()}

        cold = RUNS / "cold-rag-v1" / "analysis" / "cold-rag-analysis.json"
        cold_out = temp / "cold-rag-analysis.json"
        matrix = Path(json.loads(cold.read_text(encoding="utf-8"))["runs"][0]["directory"]).parent
        run(["cold_rag/analyze_cold_rag.py", "--input", str(RUNS / "cold-rag-v1" / "runs"),
             "--output", str(cold_out), "--directory-prefix", str(matrix)])
        result["rebuild"]["cold-rag"] = {"path": str(cold), "sha256": sha(cold),
                                         "rebuilt_sha256": sha(cold_out),
                                         "equal": cold.read_bytes() == cold_out.read_bytes()}

        fault = RUNS / "retest-v12" / "analysis" / "fault-analysis.json"
        fault_out = temp / "fault-analysis.json"
        run(["analyze_fault.py", "--input", str(RUNS / "retest-v12" / "fault"),
             "--output", str(fault_out)])
        old = json.loads(fault.read_text(encoding="utf-8"))
        new = json.loads(fault_out.read_text(encoding="utf-8"))
        old_rows = {(row["policy"], row["host"], row["repeat"]): row for row in old["rows"]}
        new_rows = {(row["policy"], row["host"], row["repeat"]): row for row in new["rows"]}
        fields = ("detection_s", "engine_health_s", "router_reaccept_s", "pass",
                  "after_removal_ok", "after_recovery_ok", "recovered_target_probes")
        mismatches = []
        for key, old_row in old_rows.items():
            new_row = new_rows.get(key)
            if new_row is None:
                mismatches.append({"key": key, "field": "row", "old": "present", "new": "missing"})
                continue
            for field in fields:
                if old_row.get(field) != new_row.get(field):
                    mismatches.append({"key": key, "field": field,
                                       "old": old_row.get(field), "new": new_row.get(field)})
        result["rebuild"]["fault"] = {
            "path": str(fault), "sha256": sha(fault),
            "cycles": {"old": len(old_rows), "new": len(new_rows)},
            "headline_fields_equal": not mismatches, "mismatches": mismatches,
            "new_first_target_verified": new["summary"].get("first_target_verified"),
            "sha256_note": "fault rows gained first-target and preflight evidence, so byte equality is not required",
        }

        for name, path in {
            "smoke-analysis.json": BASE / "smoke-analysis.json",
            "retest-analysis.json": BASE / "retest-analysis.json",
            "fault-analysis.json": BASE / "fault-analysis.json",
            "cold-rag-analysis.json": BASE / "cold-rag-analysis.json",
        }.items():
            result["removed_root_copies"][name] = {"present": path.exists()}

    result["archives"] = {
        str(path.relative_to(BASE)): sha(path) for path in sorted(RUNS.glob("*/analysis/*.json*"))
    }
    result["pass"] = all(item["equal"] for name, item in result["rebuild"].items() if name != "fault")
    result["pass"] = result["pass"] and result["rebuild"]["fault"]["headline_fields_equal"]
    result["pass"] = result["pass"] and not any(item["present"] for item in result["removed_root_copies"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"pass": result["pass"],
                      "rebuild": {k: {kk: vv for kk, vv in v.items()
                                      if kk in ("equal", "headline_fields_equal", "sha256")}
                                  for k, v in result["rebuild"].items()},
                      "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
