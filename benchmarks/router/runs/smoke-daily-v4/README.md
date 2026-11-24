# Daily smoke acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-daily-v4`.
- Top-level: `pass=true`, wall clock 92.07 s, exit code 0; both direct engines health 200 after.
- No fault stage: no proxy `/health` toggle and no engine stop.
- Stages: router_ready 0.77 s, core 57.44 s, queue 33.59 s, analysis 0.26 s.
- Core: 28/28 success, 28 protocol checks, 28 quality checks, `policy_ok=true`; recall variants 4/4.
- Queue: 12 long requests, 2 cancelled, 10 completed (9 success + 1 truncated); engine and Router counters recovered in 0.06 s.
- Top-level summary: `.verification/dual-qwen/smoke-daily-v4/summary.json`
- Registered copies: `summaries/cache_aware-summary.json`,
  `summaries/cache_aware-requests.jsonl`, `summaries/queue-cache_aware.json`,
  `summaries/run-summary.json`, `analysis/smoke-analysis.json`.
