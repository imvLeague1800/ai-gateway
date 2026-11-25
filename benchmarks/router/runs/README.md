# Registered run evidence

As of 2026-11-25 this Git tree holds **no run registrations**. All previously registered
runs, including the current acceptance evidence and the offline-recompute inputs
(`smoke-v1`, `retest-v12`, `cold-rag-v1`), were copied to persistent storage and removed
from the tree. Git now keeps code, docs and the manifest/smoke fingerprints only.

## Persistent archive

- `X:\damonlu\ai-gateway\router-runs-archive\20261125` (share `\\10.10.18.151\damonlu`):
  the 11 earlier runs (`smoke-daily-v1` … `v6`, `smoke-entry-v1`, `smoke-proxy-v6/v10/v12`,
  `fault-20261124-1x-r2`).
- `X:\damonlu\ai-gateway\router-runs-archive\20261125\current-acceptance`:
  `smoke-daily-v7`, `smoke-daily-v8`, `stress-20261124-live`,
  `stress-failinject-20261124`, `preflight-20261124`, `fault-20261124-newpreflight`,
  `fault-20261124-startfail`, `smoke-v1`, `retest-v12`, `cold-rag-v1`.

Each location has an `INDEX.json` with the run ID, file count, byte size and per-file
raw-byte SHA-256 verification against the local source. Locate historical evidence by run
ID plus the index; the results report keeps the historical numbers.

## Working with the archive

`run_smoke.py`, `run_stress.py`, `run_retest.py` and the registration scripts still write
new evidence under `runs/<run-id>/` locally. Copy and verify a run into the persistent
archive before removing it from Git; do not commit new evidence trees here.

`verify_archives.py` and the analysis commands below expect the three input directories
(`smoke-v1`, `retest-v12`, `cold-rag-v1`) under `runs/`. Restore them from
`current-acceptance` into `runs/` first if offline recomputation is required:

```bash
sha256sum -c runs/retest-v12/evidence-sha256.txt   # after restoring the run
cd benchmarks/router
python analyze_retest.py --input runs/retest-v12 --output /tmp/retest-analysis.json
python analyze_fault.py --input runs/retest-v12/fault --output /tmp/fault-analysis.json
python analyze_smoke.py --input runs/smoke-v1 --output /tmp/smoke-analysis.json
python cold_rag/analyze_cold_rag.py --input runs/cold-rag-v1/runs --output /tmp/cold-rag-analysis.json
```

This directory keeps only this README; analysis output goes to the requested path, and the
daily default is a run-ID directory under `.verification/dual-qwen/`.
