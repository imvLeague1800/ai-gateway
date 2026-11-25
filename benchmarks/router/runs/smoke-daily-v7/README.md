# Daily smoke acceptance — 2026-11-25

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-daily-v7`.
- Manifest at run time: `67d067871e806ccf0d91f68665435fc8683778c5e751581ef3377e62dbb378f0` (v1.3.2, after replay completeness and diagnostic-script cleanup).
- Top-level: `pass=true`, wall clock 89.55 s, exit code 0; both direct engines health 200 after.
- No fault stage: no proxy `/health` toggle and no engine stop.
- Stages: router_ready 0.93 s, core 57.19 s, queue 31.13 s, analysis 0.29 s.
- Core: 28/28 success, 28 protocol checks, 28 quality checks, `policy_ok=true`; recall variants 4/4; cache hit ratio 0.2738 (92,224 / 336,848 tokens).
- Queue: 12 long requests, 2 cancelled (`queue-0`, `queue-11`), 10 completed (all success); engine and Router counters recovered in 0.10 s.
- Top-level summary: `.verification/dual-qwen/smoke-daily-v7/summary.json`
- Registered copies: `summaries/cache_aware-summary.json`,
  `summaries/cache_aware-requests.jsonl`, `summaries/queue-cache_aware.json`,
  `summaries/run-summary.json`, `analysis/smoke-analysis.json`.
