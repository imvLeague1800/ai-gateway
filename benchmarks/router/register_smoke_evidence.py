"""Register compact smoke evidence under runs/<run-id>/ and hash the raw tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
DEFAULT_RAW = ROOT / ".verification" / "dual-qwen" / "smoke"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--analysis", type=Path)
    args = parser.parse_args()
    raw = args.input.resolve()
    dst = BASE / "runs" / args.run_id
    (dst / "summaries").mkdir(parents=True, exist_ok=True)
    (dst / "concurrency").mkdir(parents=True, exist_ok=True)
    (dst / "analysis").mkdir(parents=True, exist_ok=True)
    index = []
    for policy in ("cache_aware", "power_of_two"):
        for name in ("summary.json", "requests.jsonl"):
            path = raw / policy / name
            if path.exists():
                shutil.copy2(path, dst / "summaries" / f"{policy}-{name}")
                index.append({"kind": "core", "policy": policy, "file": name,
                              "sha256": sha(path), "bytes": path.stat().st_size})
        q = raw / f"queue-{policy}" / "queue-summary.json"
        if q.exists():
            shutil.copy2(q, dst / "summaries" / f"queue-{policy}.json")
            index.append({"kind": "queue", "policy": policy, "file": q.name,
                          "sha256": sha(q), "bytes": q.stat().st_size})
    for scenario in ("agent", "rag_cold", "rag_warm"):
        for c in (1, 2, 3, 4):
            directory = raw / "concurrency" / f"{scenario}-c{c}"
            for name in ("summary.json", "requests.jsonl"):
                path = directory / name
                if path.exists():
                    shutil.copy2(path, dst / "concurrency" / f"{scenario}-c{c}-{name}")
                    index.append({"kind": "concurrency", "scenario": scenario, "concurrency": c,
                                  "file": name, "sha256": sha(path), "bytes": path.stat().st_size})
    for name in ("failure-summary.json", "failover/summary.json", "summary.json"):
        path = raw / name
        if path.exists():
            flat = "failover-summary.json" if name.startswith("failover/") else (
                "run-summary.json" if name == "summary.json" else name)
            shutil.copy2(path, dst / "summaries" / flat)
            index.append({"kind": "aux", "file": flat, "sha256": sha(path),
                          "bytes": path.stat().st_size})
    analysis = args.analysis or raw / "analysis" / "smoke-analysis.json"
    if analysis.exists():
        shutil.copy2(analysis, dst / "analysis" / "smoke-analysis.json")
    with (dst / "run-index.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(json.dumps(row, ensure_ascii=True) for row in index) + "\n")

    lines = []
    selected = [raw / p for p in ("cache_aware", "power_of_two", "queue-cache_aware", "queue-power_of_two")]
    selected += [raw / "concurrency" / f"{s}-c{c}" for s in ("agent", "rag_cold", "rag_warm")
                 for c in (1, 2, 3, 4)]
    selected += [raw / "failure-summary.json", raw / "failover", raw / "summary.json",
                 raw / "analysis" / "smoke-analysis.json"]
    for path in selected:
        if path.is_file():
            lines.append(f"{sha(path)}  {path.relative_to(ROOT).as_posix()}")
        elif path.exists():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    lines.append(f"{sha(child)}  {child.relative_to(ROOT).as_posix()}")
    with (dst / "evidence-sha256.txt").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(json.dumps({"run_id": args.run_id, "input": str(raw), "destination": str(dst),
                      "files": len(index), "hashed": len(lines),
                      "run_index_sha256": sha(dst / "run-index.jsonl"),
                      "evidence_sha256": sha(dst / "evidence-sha256.txt")}, indent=2))


if __name__ == "__main__":
    main()
