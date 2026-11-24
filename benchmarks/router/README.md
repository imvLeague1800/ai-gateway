# Router Benchmark Assets

Versioned replay assets for the dual-Qwen `power_of_two` vs `cache_aware` router
comparison. This directory is intended to be reviewed and committed; large raw
run outputs belong in internal persistent storage and are referenced by run ID.

## Status

- `cases.jsonl` / `sessions.jsonl`: synthetic, frozen validation corpus (300
  scored cases, 195 sessions).
- `calibration_cases.jsonl` / `calibration_sessions.jsonl`: disjoint synthetic
  calibration partition (60 cases, 36 sessions), used only to freeze completion
  budgets.
- **Real Agent sessions: 0.** No company-authorized real sessions were supplied
  for this round. The real-Agent acceptance is therefore marked *pending*; no
  synthetic session is labeled as real.

## Entry points

Daily benchmark has two groups: **smoke** and **stress**. Both expect the
isolated Router and both actual Qwen engines to be ready (`.15:8000`,
`.16:8000`), with the local SSH tunnel mapping `18100 -> .16:8100` and
`19100 -> .16:29100`. Neither daily group stops or starts an engine.

The manual real-fault drill is separate and is run only when the user explicitly
requests it.

### Daily smoke: 5–10 minutes

Default smoke runs the frozen 16-session/28-request candidate subset and one
bounded queue/cancel check. It does **not** restart the Router for a fault or stop
Qwen. It rejects an existing `--run-id` directory
before any stage, so an older `pass=true` summary cannot be reused. Core and
queue child exit codes, their fresh summaries, analysis, and final health of both
direct engines all participate in the top-level `pass` result.

```bash
python run_smoke.py --policy cache_aware
python run_smoke.py --policy cache_aware --run-id smoke-20261124
python run_smoke.py --policy cache_aware --baseline    # optional same-subset baseline
python run_smoke.py --policy cache_aware --no-restart  # caller already started the isolated Router
```

The run writes `<run-id>/summary.json`, per-stage logs, the candidate replay,
queue evidence, and `analysis/smoke-analysis.json` under
`.verification/dual-qwen/`. `--baseline` restarts the Router for the other policy
and restores the candidate in a `finally` block. Per-stage and total wall-clock
time are recorded.

Aggregate or register an already completed run:

```bash
python analyze_smoke.py --input ../../.verification/dual-qwen/<run-id> \
  --output ../../.verification/dual-qwen/<run-id>/analysis/smoke-analysis.json
python register_smoke_evidence.py --run-id <run-id> \
  --input ../../.verification/dual-qwen/<run-id>
```

Registration writes the selected summaries, queue evidence, analysis, run index
and SHA-256 list under `runs/<run-id>/`; it does not overwrite a fixed historical
directory.

### Daily stress: scenario-based

The stress entry orchestrates the full replay, direct 1–4 lane matrix, and the
long-cold RAG matrix. Run only the modes needed for the change. It has no fault
mode and never stops an engine; serialize it with smoke or the manual drill if
they share the same Spark.

```bash
python run_stress.py --modes replay concurrency cold-rag
```

A failed replay round is not swallowed. `run_retest.py` writes that round's
group, profile, policy, raw exit code and log path to `run-index.jsonl` as soon
as it finishes, keeps later rounds' records, and exits non-zero once any round
failed. `run_stress.py` marks the `replay` stage and the whole group as failed,
keeps the stage exit code and log path plus the replay `run-index.jsonl` path in
its summary, and exits non-zero. Later successful rounds never erase an earlier
failure.

Equivalent individual entries, with explicit run IDs/output directories:

```bash
python run_retest.py --run-id <id> --output ../../.verification/dual-qwen/<id>/replay
python run_smoke_concurrency.py --output ../../.verification/dual-qwen/<id>/concurrency
python cold_rag/run_cold_rag_matrix.py --output ../../.verification/dual-qwen/<id>/cold-rag
```

### Manual real-fault drill: explicit request only

This is the only entry that stops an engine. It stops and restores **only** the
`.16` Qwen Compose service:

```bash
cd /home/dgx/spark-vllm
docker compose --profile qwen stop qwen    # drill stop
docker compose --profile qwen start qwen   # drill recovery
```

`.15` Qwen and the gateway continue running. The drill uses the already proven
`dgx@10.169.25.16` SSH account and the `/home/dgx/spark-vllm` Compose project; it
performs no authorization inference or record check. Do not run it from a daily
job, CI or a timer.

Before running, the user must explicitly request the drill and specify one cycle
or a count, and the operator must confirm the maintenance window. The entry
refuses to start without `--maintenance-window-ack` and refuses an existing run
ID. `--preflight-only` is exempt from the acknowledgement because it never stops
or starts anything.

Preflight is read-only and runs before any stop/start command:

1. SSH batch login as `dgx@10.169.25.16` and the executing account (`id -un`),
   both recorded in the summary.
2. The `/home/dgx/spark-vllm` Compose project and its `qwen` service are identified
   from the Compose labels; the container must be running.
3. Both direct engines are healthy, and `.16` serves `qwen3.6-35b-a3b-nvfp4`.
4. The isolated Router is ready with both direct Qwen workers. When it is not
   ready, the drill starts it only after these read-only checks pass; an already
   ready Router with the requested policy is reused.

Preflight runs no Compose dry-run and makes no authorization-record, Docker-socket
or sudo check. If SSH login, the target project/service, either engine, the model,
or Router readiness for `--preflight-only` fails, preflight exits with code 2 and
records the reason; no cycle starts and no recovery start is attempted.

`--preflight-only` runs the same read-only checks, then exits without starting or
removing the Router and without touching Qwen.

```bash
# read-only readiness check
python run_fault_drill.py --preflight-only --run-id preflight-20261124

# single cycle
python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124

# multiple cycles; only .16 is stopped each time
python run_fault_drill.py --cycles 3 --maintenance-window-ack --run-id fault-3x-20261124
```

The drill uses an isolated Router with both Qwen workers direct, default
`cache_aware`, health interval 5 s / failure 2 / success 2, and keeps that same
Router container through every stop/recover cycle. Per cycle it records:

- pre-stop direct health and at least one complete `.16` Router response;
- four in-flight long requests and their completed/interrupted states;
- the stop command result, direct `.16` health failure, and Router removal time
  from the stop command (20 s target);
- all post-removal probes complete on `.15`;
- the start command, the first observed `.16:8000/health` HTTP 200 timestamp
  (saved inside the health poll, even when the start command has not returned
  yet), Router re-accept time, and the first complete `.16` response through the
  Router;
- the recovery gap, computed on one monotonic clock as the end of that first
  complete `.16` response minus the first direct health 200. The target is
  20.00 s: exactly 20.00 passes and 20.01 fails. A missing timestamp, a response
  attributed to `.15`, an incomplete stream, or a missing request ID fails the
  cycle;
- start-to-direct-health and direct-health-to-first-complete-response reported
  separately, with both timestamps, the request ID, the actual worker and the
  stream completion state;
- final direct health of both engines and `.16` Router acceptance.

If a stop was issued, recovery is attempted in a `finally` block even when a
probe or command raises. An SSH/service state that cannot be confirmed is a
failure. If recovery or `.16` acceptance is not confirmed, the drill writes the
failed cycle, stops starting further cycles, and exits non-zero.

Results go to `.verification/dual-qwen/<run-id>/summary.json` and
`cycle-<nn>.json`. The drill removes a Router it started unless `--keep-router`
is passed; a reused Router is left running. Daily smoke/stress are never blocked
by this entry.

Historical mock and proxy evidence remains under `runs/` as archived results; the
scripts that produced it were removed with the old fault path.

## Files

| File | Purpose |
| --- | --- |
| `manifest.json` | Dataset version, counts, source, seed, schema and SHA-256 for every asset. |
| `cases.jsonl` | One scored request per line: stable id, scenario/kind, messages, tools, expected answer/tool call, scoring method, TTFT budget. |
| `sessions.jsonl` | Session id, ordered steps, parent step and inter-step gap. Step dependencies are checked by `replay.py`. |
| `profiles.json` | Mix (40/30/30), session counts, stable subset, rates (high 2.0 after calibration, light 1.5, stable 0.75), seed and tail. |
| `calibration_cases.jsonl`, `calibration_sessions.jsonl` | Disjoint calibration set. |
| `calibration-results.json`, `budgets.json` | Measured calibration completion times and frozen completion budgets. |
| `generate.py`, `prompts.py` | Reproducible synthetic generator (seed 20261123). |
| `transport.py` | Streaming client, metric parser, quality/state classification. |
| `replay.py` | Fixed-arrival replay, 1-second metric sampling, scoring and raw evidence. |
| `make_calibration.py` | Rebuilds the calibration files and budgets. |
| `smoke.json`, `run_smoke.py` | Frozen 16-session/28-request daily smoke (no fault) against the actual Router and engines. |
| `run_fault_drill.py` | Manual real-fault drill: read-only preflight, optional `--preflight-only`, stop/restore only `.16` Qwen for one or more cycles, recovery evidence. |
| `smoke_replay.py`, `smoke_queue.py`, `smoke_concurrency.py`, `run_smoke_concurrency.py` | Core replay, bounded queue/cancel, direct 1–4 lane concurrency and its matrix runner. |
| `run_stress.py`, `run_retest.py` | Stress entry (replay/concurrency/cold-rag) and full 3-group paired replay. A replay failure stops the stress entry. |
| `analyze_smoke.py`, `analyze_retest.py`, `analyze_fault.py`, `render_smoke_tables.py`, `verify_archives.py` | Analysis entry points (`--input`/`--output`, optional `--run-id`) and offline archive reconstruction check. |
| `register_smoke_evidence.py`, `cold_rag/register_cold_rag_evidence.py` | Register selected evidence under `runs/<run-id>/`. |
| `cold_rag/backends-template.yaml`, `cold_rag/cold_rag_replay.py`, `cold_rag/run_cold_rag_matrix.py`, `cold_rag/analyze_cold_rag.py`, `cold_rag/config_probe.py`, `scenario_stats.py` | First-version Scheduler long-cold admission comparison, generated per-run configs, shared window/statistics, and isolated config probes. |

## Frozen budgets

TTFT budgets are part of each case: QA 2 s, cold Agent/RAG 10 s, continuation
5 s. Completion budgets are frozen in `budgets.json` from the disjoint
calibration run:

| kind | completion budget |
| --- | ---: |
| qa | 2,000 ms |
| agent_cold | 5,000 ms |
| agent_warm | 5,000 ms |
| rag_cold | 10,000 ms |
| rag_warm | 5,000 ms |

## Dataset and runtime checks

```bash
python replay.py validate
python run_fault_drill.py --preflight-only --run-id <run-id>  # read-only readiness
python run_smoke.py --run-id <run-id>                         # actual Router and engines
python make_calibration.py         # only when intentionally re-freezing budgets
```

Explicit replay examples:

```bash
python replay.py stable --policy power_of_two --group 1 --namespace ns-... \
  --budgets budgets.json --output ../../.verification/dual-qwen/retest
python replay.py high --policy cache_aware --group 1 --namespace ns-... \
  --budgets budgets.json --output ../../.verification/dual-qwen/retest
python replay.py light --policy power_of_two --group 1 --namespace ns-... \
  --budgets budgets.json --output ../../.verification/dual-qwen/retest
```

The client uses `ROUTER_API` and `ROUTER_METRICS` environment variables
(defaults `http://127.0.0.1:18100` and `http://127.0.0.1:19100/metrics`).

## Archived evidence

The versioned archives are the only long-term copies of the four analysis
files; the former root-level `smoke-analysis.json`, `retest-analysis.json`,
`fault-analysis.json` and `cold-rag-analysis.json` were removed after SHA-256
verification:

```text
runs/smoke-v1/analysis/smoke-analysis.json
runs/retest-v12/analysis/retest-analysis.json
runs/retest-v12/analysis/fault-analysis.json
runs/cold-rag-v1/analysis/cold-rag-analysis.json
```

`runs/retest-v12/analysis/request-digests.jsonl` stores compact per-request
`{run, state, ttft_ms}` records so the retest analysis can be rebuilt offline
without the Git-ignored raw request streams. The archived smoke, retest, fault
and cold analyses can be rechecked from `runs/` with the commands in
`runs/README.md`; no Router or engine is needed. Run the combined check with:

```bash
python verify_archives.py
```

## Scoring and state rules

- TTFT: client monotonic clock to the first non-empty content or tool-call
  delta. Completion: client monotonic clock to the end of the stream.
- P50/P95/P99 use the same nearest-rank algorithm in `replay.percentile`.
- Cache token ratio: sum of engine `prefix_cache_hits_total` deltas divided by
  sum of `prefix_cache_queries_total` deltas over the window. Concurrent
  samples are attributed only to worker and window; no per-request attribution.
- Request state (mutually exclusive): `success`, `rejected` (429), `http_error`,
  `timeout`, `stream_interrupted` (HTTP 200 then incomplete stream),
  `client_exception`, `quality_failure`, `truncated` (finish_reason=length).
- Fixed-window qualified RPS: successful, quality-pass, and within the frozen
  TTFT/completion budgets, admitted before the fixed window end, divided by the
  frozen window length. Drain time is reported separately and is not part of the
  denominator.

## Isolation and provenance

- Router image: `vllm/vllm-router:v0.1.17`, arm64; record the exact digest in
  each run metadata.
- Workers: `.15:8000` and `.16:8000`, same Qwen image/weights/tokenizer/template.
- Both policies use `cache_threshold=0.3`, `balance_abs_threshold=2`,
  `balance_rel_threshold=1.2`, health interval 5 s / failure 2 / success 2.
- Every run gets a random neutral namespace; the namespace is not tied to a
  policy. It is substituted into `BENCH_NAMESPACE` to isolate engine prefix
  caches without clearing the engine.
- Run metadata records dataset manifest SHA-256, Git revision, router/engine
  endpoints, namespace, group, policy, profile and start time. Raw request rows
  retain the original client timestamps and sampled engine/Router metrics.

## Real-Agent placeholder

`real/README.md` defines where authorized real session transcripts and their
scrubbed derivatives should be placed. Until then, reports must state
`real_agent_sessions: 0` and mark the real-Agent acceptance as pending.
