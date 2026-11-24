# Dual-Qwen retest v1.2.0 — evidence registration

- Dataset: `benchmarks/router/manifest.json` v1.2.0 (300 validation cases / 195 sessions,
  60 calibration cases / 36 sessions, seed 20261123).
- Formal matrix: 3 groups × {stable, high, light} × {power_of_two, cache_aware} = 18 runs.
- Fault matrix: 12 cycles = 2 policies × 2 stopped replicas × 3 repeats.
- Hot forced: 2 pairs (`cache_aware`, hot `.15` and hot `.16`).
- Raw trees: `.verification/dual-qwen/{retest-v12,fault-v12,hot-forced-v12}`.
- `evidence-sha256.txt` covers all 117 raw files (logs, requests, metrics, summaries, fault JSON).
- `run-index.jsonl` adds per-run summary/requests/metrics hashes, byte sizes, and key counters.
- `summaries/` holds the small `summary.json` / `run.json` copies for review without the raw tree.
- `environment.json` records the engine/model/tokenizer/template hashes, Router digest and parameters, and the run-time vs current manifest hashes.
- `fault/` and `hot-forced/` hold the small JSON records; `analysis/` holds the two derived analysis files.

Reproduce the headline checks:

```bash
cd benchmarks/router
python replay.py validate
python analyze_retest.py --input runs/retest-v12 --output /tmp/retest-analysis.json
python analyze_fault.py --input runs/retest-v12/fault --output /tmp/fault-analysis.json
```

`analysis/request-digests.jsonl` stores only `{run, state, ttft_ms}` per request so the paired
analysis (including the combined p99) can be rebuilt without the Git-ignored raw request streams.
The reconstructed retest analysis is byte-identical to `analysis/retest-analysis.json`.
