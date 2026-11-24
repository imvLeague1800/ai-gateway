# Qwen Router smoke regression — evidence registration

- Input subset: `benchmarks/router/smoke.json` (16 source sessions, 28 scored requests; source
  manifest v1.2.0).
- Router smoke: `cache_aware` and `power_of_two` through the isolated Router at `.16:8100`.
  Core runs used a separate neutral 15-character namespace per policy.
- Queue/cancel: 12 concurrent long requests, two intentional cancels, engine and Router counters
  returned to zero.
- Failure: one `.16` Qwen removal; 10/10 probes returned 200 with the target stopped. This was a
  historical real-stop run. The current daily smoke uses the mock/isolated failover path and keeps
  both engines running; `smoke_failure.py` requires `--allow-real-stop` and is not a daily entry.
- Direct concurrency: `.15` only, 3 scenarios × 1–4 lanes, 3 synchronous bursts + 30s sustained.
  Each request used an isolated `httpx.AsyncClient` (one connection, no keep-alive reuse) after
  exploratory runs showed stale-socket client errors while the server access log stayed 200.
- Raw tree: `.verification/dual-qwen/smoke/` (Git-ignored). `evidence-sha256.txt` hashes the 59
  registered raw files; `run-index.jsonl` records summary/requests hashes.
- Derived analysis: `analysis/smoke-analysis.json`; environment snapshot: `environment.json`.
- Rebuild the analysis offline from this archive:
  `python analyze_smoke.py --input runs/smoke-v1 --output <output>/smoke-analysis.json`.
- Report: `docs/qwen-router-smoke-test-results.md`.
