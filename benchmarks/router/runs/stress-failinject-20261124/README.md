# Replay-failure injection — 2026-11-24

Controlled real-failure test of the replay-failure propagation: `benchmarks/router/budgets.json`
was temporarily moved aside so every `replay.py` child would exit non-zero, then restored
immediately in a `finally` block. `budgets.json` SHA-256 after restore:
`014e457edabac36f24bdb7a11f7ef5f0133b8a09350540d7a79f19563cd3ba35` (matches the manifest).

- Command: `python run_stress.py --modes replay --run-id stress-failinject-20261124`
- `run_retest.py`: 18/18 rounds returned 1 (missing `budgets.json`), each round's group, profile,
  policy, raw exit code and log path are in `replay/run-index.jsonl`; the failure list was printed
  and `run_retest.py` exited 1.
- `run_stress.py`: `replay` stage returncode 1; `replay.pass=false`; top-level `pass=false`;
  exit code 1; summary kept `replay.returncode`, `replay.log` and `replay.run_index`.
- Elapsed 44.2 s. No engine or Router was stopped or restarted for fault purposes; Router restarts
  came only from the normal replay loop.

Raw evidence: `.verification/dual-qwen/stress-failinject-20261124/`.
Registered copies: `summary.json`, `replay-run-index.jsonl`, `evidence-sha256.txt`.
