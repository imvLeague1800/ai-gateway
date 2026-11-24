# Smoke entry acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --no-restart`.
- Service was already ready: isolated Router on `.16:8100` and both actual Qwen engines
  (`.15:8000`, `.16:8000`) healthy before and after every stage.
- Core: 16 sessions / 28 requests, 28/28 success, 28 protocol/quality passes, cache
  hits 92,224 / 336,876 queries, recall variant 4/4, both engines healthy after the run.
- Queue/cancel: 12 long requests, two intentional cancels; engine and Router in-flight
  counters returned to zero in 0.07 s after the remaining requests completed.
- Failover: mock Router omitted `.16`; the request was served by actual `.15` with HTTP
  200 and a complete `OK`; both actual engines stayed at health 200 before/after.
  This run predates the benchmark-proxy failover; the current default is
  `proxy_failover.py` and does not stop either engine.
- Wall clock: 87.80 s total — core 54.58 s, queue 31.47 s, failover 1.37 s, analysis
  0.36 s. This is inside the 5–10 minute smoke target.
- Raw tree: `.verification/dual-qwen/smoke-entry-v1/` (Git-ignored).
- `evidence-sha256.txt` and `run-index.jsonl` reproduce the selected evidence hashes.

Real fault mode was not included in this smoke. Real stop/start remains an explicit
stress mode restricted to `.16`.
