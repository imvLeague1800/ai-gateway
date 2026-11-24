# Long-cold RAG admission test

Runs the first-version Scheduler (`scheduler/app`) as an isolated local instance against
`10.169.25.15:8000`. The matrix creates each run's `backends.yaml` from
`backends-template.yaml`, changing only `max_long_requests` (1 or 2); both keep
`max_concurrent_requests=2` and `long_request_threshold_tokens=8192`.

- `cold_rag_replay.py` — fixed open-loop 1:1 short-Agent/17.5K-cold-doc replay, 120s window.
- `run_cold_rag_matrix.py` — 3 paired groups, alternating limit order, starts/stops the Scheduler
  and archives each generated config.
- `analyze_cold_rag.py` — uses the shared `scenario_stats` window/statistics implementation.
- `render_cold_rag_tables.py` — prints report tables from the analysis JSON.
- `register_cold_rag_evidence.py` — compact evidence and hashes under `runs/cold-rag-v1/`.

Run from this directory:

```bash
../../../scheduler/.venv/Scripts/python.exe run_cold_rag_matrix.py
../../../scheduler/.venv/Scripts/python.exe analyze_cold_rag.py
```

The Scheduler uses `SCHEDULER_INTERNAL_API_KEY=cold-rag-test-key`, `MAX_ACTIVE_PER_PRINCIPAL=1`
and 8 rotating `user` principals. It is not the production Gateway entry point.
