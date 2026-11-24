# Stress acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_stress.py --modes replay concurrency cold-rag --run-id stress-20261124-live`.
- Top-level: `pass=true`, wall clock 5852.74 s (97.5 min), exit code 0.
- Stages: `replay` 3655.68 s, `concurrency` 1268.04 s, `cold-rag` 929.02 s; each child exit code 0.
- Replay: 18/18 rounds exit 0 (3 groups × 3 profiles × 2 policies), 80 or 300 requests per run, all request states `success`.
- Concurrency: 12/12 runs exit 0 (`agent` / `rag_cold` / `rag_warm` × 1–4 lanes, direct `10.169.25.15:8000`).
- Cold RAG: 6/6 runs exit 0 (3 paired groups × long limits 1/2 via a temporary Scheduler against `10.169.25.15:8000`), 120 s window + 15 s warmup each.
- The top-level `replay` segment records `pass=true`, `returncode=0`, the stage log and `run-index.jsonl`.
- Raw evidence: `.verification/dual-qwen/stress-20261124-live/summary.json`, `replay/run-index.jsonl`,
  `concurrency/run-index.jsonl`, `cold-rag/run-index.jsonl`, and the three stage logs.
- Registered copies: `summary.json`, `replay-run-index.jsonl`, `concurrency-run-index.jsonl`,
  `cold-rag-run-index.jsonl`, `evidence-sha256.txt`.
