# Router Benchmark Assets

Versioned replay assets for the dual-Qwen `power_of_two` vs `cache_aware` router
comparison. This directory is intended to be reviewed and committed; large raw
run outputs belong in internal persistent storage and are referenced by run ID.

## Status

- `cases.jsonl` / `sessions.jsonl`: synthetic, frozen validation corpus (300
  scored cases, 195 sessions).
- `calibration_cases.jsonl` / `calibration_sessions.jsonl`: disjoint synthetic
  calibration partition (60 cases, 36 sessions), used only to freeze completion
  budgets and to validate the harness.
- **Real Agent sessions: 0.** No company-authorized real sessions were supplied
  for this round. The real-Agent acceptance is therefore marked *pending*; no
  synthetic session is labeled as real.

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
| `test_harness.py`, `test-results.json` | Mock checks for timing, cache aggregation, state classification and scoring. |
| `make_calibration.py` | Rebuilds the calibration files and budgets. |
| `smoke.json`, `smoke_replay.py`, `smoke_queue.py`, `smoke_failure.py`, `smoke_concurrency.py` | Frozen 16-session quick regression, queue/cancel, one-replica removal and direct 1–4 lane concurrency. |
| `cold_rag/backends-template.yaml`, `cold_rag/cold_rag_replay.py`, `cold_rag/run_cold_rag_matrix.py`, `cold_rag/analyze_cold_rag.py`, `cold_rag/config_probe.py`, `scenario_stats.py` | First-version Scheduler long-cold admission comparison, generated per-run configs, shared window/statistics, and isolated config probes; separate `.verification/dual-qwen/cold-rag/` evidence. |

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

## Commands

Run from this directory with a Python environment containing `httpx`.

```bash
python replay.py validate
python test_harness.py
python smoke_replay.py --policy cache_aware --output ../../.verification/dual-qwen/smoke/cache_aware
python cold_rag/run_cold_rag_matrix.py
python cold_rag/analyze_cold_rag.py
python cold_rag/config_probe.py
python make_calibration.py         # only when intentionally re-freezing budgets
python run_retest.py               # 3 paired groups x stable/high/light
python fault_retest.py --policy power_of_two --host 16 --repeat 1
python fault_retest.py --policy cache_aware --host 15 --repeat 1
python hot_forced.py --target 15 --round 1
python analyze_retest.py
python replay.py stable --policy power_of_two --group 1 --namespace ns-... \
  --budgets budgets.json --output ../../.verification/dual-qwen/retest
python replay.py high --policy cache_aware --group 1 --namespace ns-... \
  --budgets budgets.json --output ../../.verification/dual-qwen/retest
python replay.py light --policy power_of_two --group 1 --namespace ns-... \
  --budgets budgets.json --output ../../.verification/dual-qwen/retest
```

The client uses `ROUTER_API` and `ROUTER_METRICS` environment variables
(defaults `http://127.0.0.1:18100` and `http://127.0.0.1:19100/metrics`). The
isolated router listens on `.16:8100`; the local SSH tunnel maps 18100/19100.

## Scoring and state rules

- TTFT: client monotonic clock to the first non-empty content or tool-call
  delta. Completion: client monotonic clock to the end of the stream.
- P50/P95/P99 use the same nearest-rank algorithm in `replay.percentile`.
- Cache token ratio: sum of engine `prefix_cache_hits_total` deltas divided by
  sum of `prefix_cache_queries_total` deltas over the window. Concurrent
  samples are attributed only to worker and window; no per-request attribution.
- Request state (mutually exclusive): `success`, `rejected` (429), `http_error`,
  `timeout`, `stream_interrupted` (HTTP 200 then incomplete stream),
  `quality_failure`, `truncated` (finish_reason=length).
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
