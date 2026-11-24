# Proxy smoke acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-proxy-v10`.
- Top-level: `pass=true`, wall clock 127.14 s, exit code 0; both actual engines health 200 after.
- Router: same container `b088950c094d` through normal/failure/recovery (no restart during the toggle).
- Failover Router launch: `WORKER_B=http://127.0.0.1:18016 /tmp/router-benchmark-start-router.sh cache_aware`;
  health interval 5 s, timeout 3 s, failure threshold 2, success threshold 2.
- Normal: 20/20 probes attributed to proxy worker `http://127.0.0.1:18016`; first complete response 0.54 s.
  Routing was load-steered: a long request kept `.15` busy while the probe target `.16` was idle.
- Failure: `/health` switched to 503 at `2026-11-24T08:13:45.571Z`; Router marked `.16` unhealthy at
  `2026-11-24T08:13:55.102Z` (9.95 s). 20/20 probes were attributed to actual `.15` and complete;
  direct `.16:8000` stayed 200.
- Recovery: passthrough restored at `2026-11-24T08:13:58.796Z`; Router reaccepted `.16` at
  `2026-11-24T08:14:05.272Z` (6.96 s). The first complete `.16` response started at
  `2026-11-24T08:14:05.556Z` and ended at `2026-11-24T08:14:05.668Z` (7.37 s after recovery);
  request ID `af24d4d6bce24fd3a1fd519da6ba406f` appears in the proxy forwarding log.
  All 20 recovery probes were attributed to the proxy worker.
- Cleanup: proxy process stop explicitly confirmed, Router restored to direct `.16:8000` at
  `2026-11-24T08:14:13.486Z`, both direct engines 200.
- Top-level summary: `.verification/dual-qwen/smoke-proxy-v10/summary.json`
- Failover summary: `.verification/dual-qwen/smoke-proxy-v10/failover/summary.json`
- Registered copies: `summaries/run-summary.json`, `summaries/failover-summary.json`,
  `summaries/cache_aware-summary.json`, `summaries/queue-cache_aware.json`,
  `analysis/smoke-analysis.json`.

## Controlled gate checks (no services)

`python -m unittest test_smoke_proxy.py test_run_smoke.py` — 24/24 passed.

| Case | Evidence | Result |
| --- | --- | --- |
| Remote explicitly confirms proxy exit | `test_stop_proxy_confirmed`, `test_confirmed_stop_restores_direct_and_passes` | `proxy_stopped=true`, fault-phase exit 0 |
| SSH 255 or timeout, stop unconfirmed | `test_ssh_255_is_unknown_not_stopped`, `test_ssh_timeout_is_unknown_not_stopped`, `test_stop_proxy_ssh_failure_raises`, `test_unconfirmed_stop_still_restores_and_fails` | error recorded, `proxy_stopped` not true, fault-phase exit 1, direct restore still called |
| Existing run ID with old `pass=true` summary | `test_existing_run_id_rejected_before_any_stage` | rejected with exit 2 before any stage; no subprocess or Router restart |
| New run ID, child exit 1 but writes `pass=true` | `test_nonzero_child_fails_even_with_pass_true_summary` | top-level exit 1, child exit code preserved |
| Child exit 0, summary missing/invalid/`pass!=true`/cleanup health missing | `test_missing_summary_fails`, `test_invalid_summary_fails`, `test_pass_not_true_fails`, `test_cleanup_health_missing_fails` | top-level exit 1 |
| Child exit 0 with valid fresh `pass=true` summary | `test_fresh_pass_summary_passes` | top-level exit 0 |
