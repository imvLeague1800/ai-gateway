# Read-only drill preflight acceptance — 2026-11-24

Proves `--preflight-only` is read-only: it checks the `.16` account, Compose project/service,
both direct engines, the `.16` model and the isolated Router, then exits without starting or
removing the Router and without stopping/starting Qwen.

Commands and results:

| run ID | command | Router before | exit | stop/start |
| --- | --- | --- | ---: | --- |
| `preflight-20261124-a` | `python run_fault_drill.py --preflight-only --run-id preflight-20261124-a` | not ready | 2 | 0 / 0 |
| `preflight-20261124-b` | `python run_fault_drill.py --preflight-only --run-id preflight-20261124-b` | ready `cache_aware` | 0 | 0 / 0 |

Both runs: `dgx@10.169.25.16` login and `id -un` = `dgx`; Compose project
`/home/dgx/spark-vllm`, service `qwen`, state `running`; direct health `{"15": 200, "16": 200}`;
`.16` model `qwen3.6-35b-a3b-nvfp4`. Run A reports the Router as not ready and refuses to
start it (4.44 s). Run B finds the Router ready with policy `cache_aware`
(container `40d86e0a8b4e`) and passes (3.29 s). Neither run added to
`actual_engines_stopped`; no Router removal was attempted.

Registered files: `preflight-only-router-down.json`,
`preflight-only-router-ready.json`, `evidence-sha256.txt`. Raw summaries:
`.verification/dual-qwen/preflight-20261124-a/summary.json` and
`.verification/dual-qwen/preflight-20261124-b/summary.json`.
