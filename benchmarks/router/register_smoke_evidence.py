"""Register compact smoke evidence and hash the raw smoke trees."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
RAW = ROOT / ".verification" / "dual-qwen" / "smoke"
DST = BASE / "runs" / "smoke-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    (DST / "summaries").mkdir(parents=True, exist_ok=True)
    (DST / "concurrency").mkdir(parents=True, exist_ok=True)
    (DST / "analysis").mkdir(parents=True, exist_ok=True)
    index = []
    for policy in ("cache_aware", "power_of_two"):
        for name in ("summary.json", "requests.jsonl"):
            path = RAW / policy / name
            if path.exists():
                shutil.copy2(path, DST / "summaries" / f"{policy}-{name}")
                index.append({"kind": "core", "policy": policy, "file": name,
                              "sha256": sha(path), "bytes": path.stat().st_size})
        q = RAW / f"queue-{policy}" / "queue-summary.json"
        if q.exists():
            shutil.copy2(q, DST / "summaries" / f"queue-{policy}.json")
            index.append({"kind": "queue", "policy": policy, "file": q.name,
                          "sha256": sha(q), "bytes": q.stat().st_size})
    for scenario in ("agent", "rag_cold", "rag_warm"):
        for c in (1, 2, 3, 4):
            directory = RAW / "concurrency" / f"{scenario}-c{c}"
            for name in ("summary.json", "requests.jsonl"):
                path = directory / name
                if path.exists():
                    shutil.copy2(path, DST / "concurrency" / f"{scenario}-c{c}-{name}")
                    index.append({"kind": "concurrency", "scenario": scenario, "concurrency": c,
                                  "file": name, "sha256": sha(path), "bytes": path.stat().st_size})
    failure = RAW / "failure-summary.json"
    if failure.exists():
        shutil.copy2(failure, DST / "summaries" / "failure-summary.json")
        index.append({"kind": "failure", "file": failure.name, "sha256": sha(failure),
                      "bytes": failure.stat().st_size})
    analysis = BASE / "smoke-analysis.json"
    if analysis.exists():
        shutil.copy2(analysis, DST / "analysis" / "smoke-analysis.json")
    (DST / "run-index.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in index) + "\n", encoding="utf-8")

    lines = []
    selected = [RAW / p for p in ("cache_aware", "power_of_two", "queue-cache_aware", "queue-power_of_two")]
    selected += [RAW / "concurrency" / f"{s}-c{c}" for s in ("agent", "rag_cold", "rag_warm") for c in (1, 2, 3, 4)]
    selected += [RAW / "failure-summary.json"]
    for path in selected:
        if path.is_file():
            lines.append(f"{sha(path)}  {path.relative_to(ROOT).as_posix()}")
        elif path.exists():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    lines.append(f"{sha(child)}  {child.relative_to(ROOT).as_posix()}")
    (DST / "evidence-sha256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(index), "hashed": len(lines),
                      "run_index_sha256": sha(DST / "run-index.jsonl"),
                      "evidence_sha256": sha(DST / "evidence-sha256.txt")}, indent=2))


if __name__ == "__main__":
    main()
