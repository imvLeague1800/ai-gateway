# Manual real-fault drill with the read-only preflight — 2026-11-24

- Entry point: `benchmarks/router/run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124-newpreflight`.
- Preflight was read-only: `dgx@10.169.25.16` login and `id -un` = `dgx`; Compose project `/home/dgx/spark-vllm`, service `qwen`,
  state `running` (container `535c2a8243d4…`); direct health `{"15": 200, "16": 200}`; `.16` model
  `qwen3.6-35b-a3b-nvfp4`. No Compose dry-run, Docker-socket/group/sudo inference or authorization record was used.
- Router: already ready with policy `cache_aware` (container `939384f98d24`), so it was reused (`started_by_drill=false`,
  `reused=true`) and left running after the cycle.
- Cycle 1: `pass=true`. Stop command 11.15 s, direct `.16` failure 2.84 s, Router removal 6.93 s from the stop command.
  Post-removal probes: 20/20 complete on `.15`. Four in-flight requests: 2 stream-interrupted after HTTP 200, 2 truncated.
- Recovery: start command `12:32:48.976Z`; first direct `.16` health 200 `12:36:32.424Z` (223.45 s engine load). Router
  reaccepted in 5.59 s; first complete `.16` response ended `12:36:38.659Z`, i.e. **6.235 s** after the first direct health
  (≤20.00 s). Request ID `2bcca0eb8a8f437c9632a89837152cc5`, worker `http://10.169.25.16:8000`, `done=true`,
  `finish_reason=stop`.
- Same Router container `939384f98d24` throughout; final direct health both 200; exit code 0; total 263.38 s.
- Raw evidence: `.verification/dual-qwen/fault-20261124-newpreflight/summary.json` and `cycle-01.json`.
- Registered copies: `summary.json`, `cycle-01.json`, `evidence-sha256.txt`.
