# Proxy smoke acceptance — 2026-11-24

- Entry point: `benchmarks/router/run_smoke.py --policy cache_aware --run-id smoke-proxy-v6`.
- Top-level: `pass=true`, wall clock 135.29 s; both actual engines health 200 after.
- Router: same container `f41412f54b5e` through normal/failure/recovery (no restart during the toggle).
- Normal: 20/20 probes attributed to proxy worker `http://127.0.0.1:18016`; first complete response 0.17 s.
- Failure: `/health` switched to 503; Router marked `.16` unhealthy in 9.53 s; 20/20 probes attributed to actual `.15` and complete; direct `.16:8000` stayed 200.
- Recovery: Router reaccepted `.16` in 9.72 s; first complete `.16` response ended at 9.86 s. Request ID `63cad48df3b44d1780c21ce99aeefe67` appears in the proxy forwarding log; all 20 recovery probes were attributed to the proxy worker.
- Cleanup: proxy stopped, Router restored to direct `.16:8000`, both direct engines 200.
- Top-level summary: `.verification/dual-qwen/smoke-proxy-v6/summary.json`
- Failover summary: `.verification/dual-qwen/smoke-proxy-v6/failover/summary.json`
- Proxy request log: `.verification/dual-qwen/smoke-proxy-v6/failover/summary.json` under `proxy_requests`.
