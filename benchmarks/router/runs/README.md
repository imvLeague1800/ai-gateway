# Registered run evidence

This directory holds compact, reviewable registrations of completed benchmark runs. Large raw
streams stay under `.verification/` (Git-ignored); every registered run records the raw directory,
file sizes, and SHA-256 hashes so the evidence can be checked without committing hundreds of MB.

- `retest-v12/` — 2026-11-23 dual-Qwen retest, dataset v1.2.0: 18 formal paired runs, 12
  fault/recovery cycles, 2 hot-replica forced-selection pairs.
- `smoke-v1/` — 2026-11-23 quick Router regression (16 sessions/28 requests), queue/cancel,
  one replica removal, and direct 1–4 lane concurrency matrix.
- `smoke-entry-v1/` — 2026-11-24 smoke-entry acceptance with the historical mock failover:
  candidate 28 requests, bounded queue/cancel, 87.80 s wall clock, both engines healthy.
- `smoke-proxy-v6/` — 2026-11-24 earlier proxy acceptance: Router removal 9.53 s, `.15`
  probes 20/20 complete, reaccept 9.72 s, first `.16` complete response 9.86 s with proxy
  request-ID evidence, 135.29 s total.
- `smoke-proxy-v10/` — 2026-11-24 earlier strict-gate acceptance: removal 9.95 s, reaccept
  6.96 s, first `.16` complete response 7.37 s, request ID
  `af24d4d6bce24fd3a1fd519da6ba406f` in the proxy log, 127.14 s total.
- `smoke-proxy-v12/` — 2026-11-24 historical proxy acceptance on manifest v1.2.7: removal
  5.60 s, reaccept 6.75 s, first `.16` complete response 7.34 s, request ID
  `5d8abfe9d2ca401e963c80f74daee66d`, 121.54 s total. Daily smoke no longer calls the proxy.
- `smoke-daily-v1/` — 2026-11-24 earlier daily smoke: core 28/28, queue/cancel, no fault,
  no proxy and no engine stop, 91.31 s total.
- `smoke-daily-v2/` — 2026-11-24 earlier daily smoke: core 28/28, queue/cancel, no fault
  stage, 88.08 s total.
- `smoke-daily-v3/` — 2026-11-24 current daily smoke on final manifest v1.2.8: core 28/28,
  queue/cancel, no fault stage, 87.18 s total.
- `fault-20261124-1x-r2/` — 2026-11-24 manual real-fault drill (one cycle, manifest v1.2.9):
  `.16` Qwen stop/restore 8.93 s removed / 18.83 s reaccepted, first complete `.16` response
  19.469 s after direct health, request ID `50a06a4b32d74562a81afb64873e756a`, exit code 0.
- `smoke-daily-v4/` — 2026-11-24 earlier daily smoke on manifest v1.2.9: core 28/28,
  queue/cancel, no fault stage, 92.07 s total, exit code 0.
- `smoke-daily-v5/` — 2026-11-24 daily smoke on manifest v1.2.9: core 28/28,
  queue/cancel, no fault stage, 88.42 s total, exit code 0.
- `smoke-daily-v6/` — 2026-11-24 current daily smoke on manifest v1.3.0 (read-only drill
  preflight, proxy and double tests removed): core 28/28, queue/cancel, no fault stage,
  90.30 s total, exit code 0.
- `preflight-20261124/` — 2026-11-24 read-only drill preflight evidence: run A exits 2
  with the Router down and starts nothing; run B exits 0 against the ready Router. Both
  stop/start counts are zero.

To re-check an evidence tree on this machine:

```bash
sha256sum -c benchmarks/router/runs/retest-v12/evidence-sha256.txt
```

Regenerate summaries without reissuing requests:

```bash
cd benchmarks/router
python analyze_retest.py --input runs/retest-v12 --output /tmp/retest-analysis.json
python analyze_fault.py --input runs/retest-v12/fault --output /tmp/fault-analysis.json
python analyze_smoke.py --input runs/smoke-v1 --output /tmp/smoke-analysis.json
python cold_rag/analyze_cold_rag.py --input runs/cold-rag-v1/runs --output /tmp/cold-rag-analysis.json
```

Analysis entry points write to the requested output path; the daily default is a run-ID directory
under `.verification/dual-qwen/`, not this versioned archive. New registrations use
`register_smoke_evidence.py --run-id <id>` or `cold_rag/register_cold_rag_evidence.py --run-id <id>`.
