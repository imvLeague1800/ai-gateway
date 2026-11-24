# Manual real-fault drill — 2026-11-24

- Entry point: `benchmarks/router/run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124-1x-r2`.
- Scope: stopped and restored only the `.16` Qwen Compose service; `.15` and the gateway stayed up.
- Preflight authorization at run time: account `dgx`, Compose `2.39.1`; `docker compose --profile qwen --dry-run stop qwen` returned 0 with `DRY-RUN MODE` output, and a real no-op `docker start` on the running container returned 0 with identical `StartedAt` before and after. The plan was later extended: current preflight uses the effective Docker policy (or an admin record) and never issues a real `stop` or `start`.
- Cycle 1: `pass=true`; 20/20 pre-stop probes complete on `.16` (first 0.54 s); Router removal 8.93 s measured from the stop command; 20/20 removal probes complete on `.15`; 4 in-flight requests: 2 stream-interrupted after HTTP 200, 2 truncated at `finish_reason=length`.
- Recovery: start command `09:38:44.216Z`, first direct `.16` health 200 `09:42:01.702Z` (197.49 s later), Router reaccept 18.83 s, first complete `.16` response ended `09:42:21.171Z` → **19.469 s** from direct health; request ID `50a06a4b32d74562a81afb64873e756a`, worker `http://10.169.25.16:8000`, `done=true`, `finish_reason=stop`.
- Same isolated Router container `eacbab0d9872` through stop and recovery; both engines 200 and `.16` accepting through the Router at the end; Router removed.
- Exit code 0, total 252.36 s.
- Raw evidence: `.verification/dual-qwen/fault-20261124-1x-r2/summary.json` and `cycle-01.json`.
- Registered copies: `summary.json`, `cycle-01.json`.
- First attempt `fault-20261124-1x` aborted before any stop command with `KeyError: 'router_container_id'`; it remains under `.verification` only and is not registered as acceptance evidence.
