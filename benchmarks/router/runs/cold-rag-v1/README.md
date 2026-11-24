# Long-cold RAG admission retest — evidence registration

- Scheduler: first-version `scheduler/app` (commit `1e4645c`), isolated at `127.0.0.1:18080`.
- Configs: `benchmarks/router/cold_rag/backends-long1.yaml` and `backends-long2.yaml`;
  `max_concurrent_requests=2`, `max_long_requests=1/2`, threshold 8192.
- Load: 1:1 short Agent / ~17.5K cold document, fixed open-loop 0.23 req/s, 15s warmup,
  120s measurement, 3 paired groups, alternating limit order.
- Raw tree: `.verification/dual-qwen/cold-rag/matrix/` (Git-ignored).
- `run-index.jsonl` records per-run summary/requests hashes; `evidence-sha256.txt` covers the raw
  matrix tree; `environment.json` records config/script/model/scheduler versions.
- Report: `docs/qwen-router-cold-rag-retest-results.md`.
