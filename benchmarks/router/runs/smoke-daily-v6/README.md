# Daily smoke acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-daily-v6`.
- Manifest at run time: `9590ba02957e6c06fed12f861c87dc8ee9e0bc3d86824115075853c3ce4acb1a` (v1.3.0, after the read-only drill preflight and proxy/double-test removal).
- Top-level: `pass=true`, wall clock 90.30 s, exit code 0; both direct engines health 200 after.
- No fault stage: no proxy `/health` toggle and no engine stop.
- Stages: router_ready 0.94 s, core 55.35 s, queue 33.75 s, analysis 0.25 s.
- Core: 28/28 success, 28 protocol checks, 28 quality checks, `policy_ok=true`; recall variants 4/4; cache hit ratio 0.2738 (92,224 / 336,848 tokens).
- Queue: 12 long requests, 2 cancelled (`queue-0`, `queue-11`), 10 completed (all success); engine and Router counters recovered in 0.05 s.
- Top-level summary: `.verification/dual-qwen/smoke-daily-v6/summary.json`
- Registered copies: `summaries/cache_aware-summary.json`,
  `summaries/cache_aware-requests.jsonl`, `summaries/queue-cache_aware.json`,
  `summaries/run-summary.json`, `analysis/smoke-analysis.json`.
