# Daily smoke acceptance — 2026-11-25

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-daily-v8`.
- Manifest at run time: `c13b57bb794eb87605853f8e309b712c8693afe13054b5d0253b32022114ec5e` (v1.3.3, LF-normalized fingerprints; sections 4–5 only).
- Top-level: `pass=true`, wall clock 96.72 s, exit code 0; both direct engines health 200 after.
- No fault stage: no proxy `/health` toggle and no engine stop.
- Stages: router_ready 1.09 s, core 61.66 s, queue 33.69 s, analysis 0.26 s.
- Core: 28/28 success, 28 protocol checks, 28 quality checks, `policy_ok=true`; recall variants 4/4; cache hit ratio 0.2738 (92,224 / 336,848 tokens).
- Queue: 12 long requests, 2 cancelled (`queue-0`, `queue-11`), 10 completed (all success); engine and Router counters recovered in 0.29 s.
- Top-level summary: `.verification/dual-qwen/smoke-daily-v8/summary.json`
- Registered copies: `summaries/cache_aware-summary.json`,
  `summaries/cache_aware-requests.jsonl`, `summaries/queue-cache_aware.json`,
  `summaries/run-summary.json`, `analysis/smoke-analysis.json`.
