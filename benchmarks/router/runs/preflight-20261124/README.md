# Read-only drill preflight acceptance — 2026-11-24

Proves `--preflight-only` is read-only: it checks the `.16` account, Compose project/service,
both direct engines, the `.16` model and the isolated Router, then exits without starting or
removing the Router and without stopping/starting Qwen.

Commands and results:

| run ID | command | Router before | exit | stop/start |
| --- | --- | --- | ---: | --- |
| `preflight-20261124-a` | `python run_fault_drill.py --preflight-only --run-id preflight-20261124-a` | not ready | 2 | 0 / 0 |
| `preflight-20261124-b` | `python run_fault_drill.py --preflight-only --run-id preflight-20261124-b` | ready `cache_aware` | 0 | 0 / 0 |
| `preflight-20261124-c` | `python run_fault_drill.py --preflight-only --run-id preflight-20261124-c` | engines healthy but Router still marked `.16` unhealthy after the injected failed recovery | 2 | 0 / 0 |
| `preflight-20261124-d` | `python run_fault_drill.py --preflight-only --run-id preflight-20261124-d` | ready `cache_aware` after manual recovery | 0 | 0 / 0 |

Both runs: `dgx@10.169.25.16` login and `id -un` = `dgx`; Compose project
`/home/dgx/spark-vllm`, service `qwen`, state `running`; direct health `{"15": 200, "16": 200}`;
`.16` model `qwen3.6-35b-a3b-nvfp4`. Run A reports the Router as not ready and refuses to
start it (4.44 s). Run B finds the Router ready with policy `cache_aware`
(container `40d86e0a8b4e`) and passes (3.29 s). Neither run added to
`actual_engines_stopped`; no Router removal was attempted.

Runs C/D follow the injected start-failure drill (`fault-20261124-startfail`): after `.16` was
restored and healthy, the Router still reported worker health 0, so run C refused in 2.31 s
(exit 2). About 3 s later the Router re-accepted `.16`; run D then passed in 3.34 s. Both were
read-only.

Registered files: `preflight-only-router-down.json`,
`preflight-only-router-ready.json`, `preflight-after-failed-recovery.json`,
`preflight-after-manual-recovery.json`, `evidence-sha256.txt`. Raw summaries:
`.verification/dual-qwen/preflight-20261124-a/summary.json` … `-d/summary.json`.
