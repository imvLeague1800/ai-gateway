# Dual-Qwen router benchmark

This directory contains the frozen synthetic test corpus and the replay, scoring, and fault-observation tools. The corpus expands the QA, archive retrieval, and synthetic service-tool examples from the prior dual-Qwen benchmark. The assistant and tool messages embedded in follow-up prompts are frozen synthetic history. The manifest declares the corpus provenance and counts. Authorized real Agent sessions are required for the separate historical and closed-loop acceptance tests.

## Validate and replay

Run from the repository root with Python and uv. The documented environment uses vLLM Router v0.1.17, vLLM workers serving the same model alias, and a Windows client. Set `ROUTER_API`, `ROUTER_METRICS`, `WORKER_A`, `WORKER_B`, and `MODEL` to use other endpoints.

```powershell
uv run --with httpx==0.28.1 python benchmarks/router/replay.py validate
uv run --with httpx==0.28.1 python benchmarks/router/test_replay.py
ssh -N -L 18100:127.0.0.1:8100 -L 19100:127.0.0.1:29100 dgx@10.169.25.16
```

With the tunnel running in a separate shell, start an isolated Router with two healthy workers. The automated runner expects the deployment's existing remote `~/spark-vllm/reports/dual-qwen/run-router.sh`. It restarts the isolated Router between runs and alternates policy order. Use a fresh output directory for a new evidence set. For a new deployment, copy `start-router.sh` to the router host and set the remote script location in `run-pairs.ps1`. The 2026-11-23 completion budgets are in `budgets.json` and were frozen before the first paired run. For a new version, run the pair launcher with `-CalibrationOnly`, inspect its cohort completion percentiles, and freeze each P95 rounded up to the next whole second before starting paired replays. Pass a separately versioned budget file with `-BudgetFile`.

```powershell
powershell -File benchmarks/router/run-pairs.ps1 -OutputRoot .verification/dual-qwen/retest
uv run --with httpx==0.28.1 python benchmarks/router/report.py .verification/dual-qwen/retest
```

For one replay against an already configured Router:

```powershell
uv run --with httpx==0.28.1 python benchmarks/router/replay.py high --policy power_of_two --group 1 --namespace "Blue Test 1" --budgets benchmarks/router/budgets.json --output .verification/dual-qwen/retest
```

The high and light profiles use 60 two-step Agent, 45 two-step RAG, and 90 one-step QA sessions. The stable profile uses 20 Agent and 20 RAG sessions. The session order is seeded by group. A child step starts after its parent finishes and the configured gap elapses. The fixed send window is nominal requests divided by profile rate plus a predefined allowance for dependencies. The raw request file retains actual send time, planned time, prompt, tool definitions, stream events, finish reason, usage, and scoring. The metric file retains approximately one-second engine and Router observations. The report keeps late sends in the overall outcomes and excludes them from fixed-window qualified throughput.

TTFT measures the first content or function name or argument delta on a monotonic high-resolution clock. Completion measures receipt of the stream terminator and closure of the client stream. Percentiles select the observed value at the rounded zero-based index `p*(n-1)`. Execution failure rates count 429, HTTP errors, timeouts, and stream interruption after HTTP 200. Quality failures and truncation have their own mutually exclusive states. Cache token ratios aggregate valid monotonic counter intervals from both engines. Sample resets and missing samples reduce interval coverage. Aggregate engine telemetry cannot attribute cache tokens to individual requests.

To run one fault trial, first set the Router policy and verify that both backends are healthy. This command stops and restarts the named worker container and needs authorization for the worker's operational impact.

```powershell
uv run --with httpx==0.28.1 python benchmarks/router/fault.py --policy cache_aware --worker qwen16 --repeat 1
```

The hot-replica comparison requires two additional, isolated, single-worker Router endpoints to force the probe onto the hot worker. Background requests in both arms go through the main two-worker Router. The script writes the loads observed in both arms so comparability can be judged from the evidence.

```powershell
uv run --with httpx==0.28.1 python benchmarks/router/hot.py --output .verification/dual-qwen/retest/hot.json --pinned http://127.0.0.1:18101 http://127.0.0.1:18102
```

To revise the dataset, update the generation inputs, bump the manifest version in `generate.py`, regenerate the corpus, validate it, and create a new evidence set for both policies. Preserve earlier manifests, budgets and raw evidence.
