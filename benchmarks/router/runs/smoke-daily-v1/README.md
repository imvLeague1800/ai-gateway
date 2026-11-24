# Daily smoke acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-daily-v1`.
- Top-level: `pass=true`, wall clock 91.31 s, exit code 0; both direct engines health 200 after.
- No fault stage: `summary.json` has no `failover` key, no proxy `/health` toggle and no engine stop.
- Stages: router_ready 0.94 s, core 58.03 s, queue 32.07 s, analysis 0.26 s.
- Core: 28/28 success, 28 protocol checks, 28 quality checks, `policy_ok=true`; recall variants 4/4.
- Queue: 12 long requests, 2 cancelled, 10 completed; engine and Router counters recovered to 0 (0.33 s).
- Top-level summary: `.verification/dual-qwen/smoke-daily-v1/summary.json`
- Registered copies: `summaries/cache_aware-summary.json`, `summaries/cache_aware-requests.jsonl`,
  `summaries/queue-cache_aware.json`, `summaries/run-summary.json`, `analysis/smoke-analysis.json`.

## Controlled gate checks (no services)

`python -m unittest test_run_smoke.py test_fault_drill.py test_smoke_proxy.py` — 39/39 passed.
The daily summary gate rejects an existing run ID, non-zero core/queue child exits, missing
summaries, failed queue recovery, failed analysis and unhealthy engines.
