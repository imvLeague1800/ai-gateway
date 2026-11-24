"""Register compact cold-RAG evidence under runs/<run-id>/ and hash the raw matrix."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
DEFAULT_RAW = ROOT / ".verification" / "dual-qwen" / "cold-rag" / "matrix"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    raw = args.input.resolve()
    dst = args.destination or ROOT / "benchmarks" / "router" / "runs" / args.run_id
    (dst / "runs").mkdir(parents=True, exist_ok=True)
    (dst / "analysis").mkdir(parents=True, exist_ok=True)
    (dst / "probes").mkdir(parents=True, exist_ok=True)
    rows = []
    for directory in sorted(raw.glob("g*-long*")):
        if not directory.is_dir():
            continue
        summary = directory / "summary.json"
        requests = directory / "requests.jsonl"
        if not summary.exists():
            continue
        out = dst / "runs" / directory.name
        out.mkdir(exist_ok=True)
        shutil.copy2(summary, out / "summary.json")
        shutil.copy2(requests, out / "requests.jsonl")
        rows.append({"run": directory.name,
                     "summary_sha256": sha(summary), "requests_sha256": sha(requests),
                     "summary_bytes": summary.stat().st_size,
                     "requests_bytes": requests.stat().st_size})
    analysis = args.analysis or raw.parent / "analysis" / "cold-rag-analysis.json"
    if analysis.exists():
        shutil.copy2(analysis, dst / "analysis" / "cold-rag-analysis.json")
    probe_dir = raw.parent / "config-probe"
    if probe_dir.exists():
        for name in ("config-probe.json", "root-scheduler.log", "single-scheduler.log",
                     "context-scheduler.log", "single-backends.yaml", "context-reject-backends.yaml"):
            path = probe_dir / name
            if path.exists():
                shutil.copy2(path, dst / "probes" / name)
    (dst / "run-index.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n", encoding="utf-8")
    files = []
    for directory in sorted(raw.glob("g*-long*")):
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    files.append(f"{sha(path)}  {path.relative_to(ROOT).as_posix()}")
    for path in sorted(raw.glob("*.log")):
        files.append(f"{sha(path)}  {path.relative_to(ROOT).as_posix()}")
    if analysis.exists():
        files.append(f"{sha(analysis)}  {analysis.relative_to(ROOT).as_posix()}")
    recompute = dst / "analysis" / "archive-recompute.json"
    if recompute.exists():
        files.append(f"{sha(recompute)}  {recompute.relative_to(ROOT).as_posix()}")
    for path in sorted((dst / "configs").glob("*.yaml")):
        files.append(f"{sha(path)}  {path.relative_to(ROOT).as_posix()}")
    for path in sorted((dst / "probes").glob("*")):
        if path.is_file():
            files.append(f"{sha(path)}  {path.relative_to(ROOT).as_posix()}")
    (dst / "evidence-sha256.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    env = {
        "captured": "2026-11-24",
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "manifest_sha256": sha(ROOT / "benchmarks" / "router" / "manifest.json"),
        "configs": {"backends-template.yaml": sha(BASE / "backends-template.yaml")},
        "scripts": {name: sha(BASE / name) for name in
                    ("cold_rag_replay.py", "run_cold_rag_matrix.py", "analyze_cold_rag.py",
                     "register_cold_rag_evidence.py")},
        "rate": 0.23, "duration_s": 120, "warmup_s": 15,
        "scheduler_endpoint": "http://127.0.0.1:18080",
        "scheduler_code": "first-version app/, commit 1e4645c21940794d604036295d0532a977f79215",
        "model": {"engine": "http://10.169.25.15:8000", "name": "qwen3.6-35b-a3b-nvfp4",
                  "max_model_len": 262144, "max_num_seqs": 4, "max_num_batched_tokens": 8192},
        "notes": ["Qwen16 was switched to Qwen and retained; Gemma is stopped for this round.",
                  "The single early RAG marker was augmented with a per-record equal-length salt because vLLM block cache could still reuse later blocks."],
    }
    (dst / "environment.json").write_text(json.dumps(env, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": args.run_id, "runs": len(rows), "hashed": len(files),
                      "run_index_sha256": sha(dst / "run-index.jsonl"),
                      "evidence_sha256": sha(dst / "evidence-sha256.txt"),
                      "environment_sha256": sha(dst / "environment.json")}, indent=2))


if __name__ == "__main__":
    main()
