# Daily smoke acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-daily-v5`.
- Manifest at run time: `d1cffb2aac8ff7ae5389423e477d791d500513453d4736ca0f07d0dcae5bc284` (v1.2.9).
- Top-level: `pass=true`, wall clock 88.42 s, exit code 0; both direct engines health 200 after.
- No fault stage: no proxy `/health` toggle and no engine stop.
- Stages: router_ready 1.08 s, core 55.58 s, queue 31.48 s, analysis 0.26 s.
- Core: 28/28 success, 28 protocol checks, 28 quality checks, `policy_ok=true`; recall variants 4/4.
- Queue: 12 long requests, 2 cancelled, 10 completed (9 success + 1 truncated); engine and Router counters recovered in 0.13 s.
- Top-level summary: `.verification/dual-qwen/smoke-daily-v5/summary.json`
- Registered copies: `summaries/cache_aware-summary.json`,
  `summaries/cache_aware-requests.jsonl`, `summaries/queue-cache_aware.json`,
  `summaries/run-summary.json`, `analysis/smoke-analysis.json`.
