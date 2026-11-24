# Registered run evidence

This directory holds compact, reviewable registrations of completed benchmark runs. Large raw
streams stay under `.verification/` (Git-ignored); every registered run records the raw directory,
file sizes, and SHA-256 hashes so the evidence can be checked without committing hundreds of MB.

- `retest-v12/` — 2026-11-23 dual-Qwen retest, dataset v1.2.0: 18 formal paired runs, 12
  fault/recovery cycles, 2 hot-replica forced-selection pairs.
- `smoke-v1/` — 2026-11-23 quick Router regression (16 sessions/28 requests), queue/cancel,
  one replica removal, and direct 1–4 lane concurrency matrix.

To re-check an evidence tree on this machine:

```bash
sha256sum -c benchmarks/router/runs/retest-v12/evidence-sha256.txt
```

Regenerate summaries without reissuing requests:

```bash
cd benchmarks/router
python analyze_retest.py
python analyze_fault.py
```
