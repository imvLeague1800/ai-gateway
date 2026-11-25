# Registered run evidence

This directory holds compact, reviewable registrations of completed benchmark runs. Large raw
streams stay under `.verification/` (Git-ignored); every registered run records the raw directory,
file sizes, and SHA-256 hashes so the evidence can be checked without committing hundreds of MB.
Since 2026-11-25 Git keeps only the current acceptance runs and the archives that
`verify_archives.py` reads; older runs live in the internal archive described below.

- `retest-v12/` — 2026-11-23 dual-Qwen retest, dataset v1.2.0: 18 formal paired runs, 12
  fault/recovery cycles, 2 hot-replica forced-selection pairs.
- `smoke-v1/` — 2026-11-23 quick Router regression (16 sessions/28 requests), queue/cancel,
  one replica removal, and direct 1–4 lane concurrency matrix.
- `smoke-daily-v7/` — 2026-11-25 daily smoke on manifest v1.3.2 (replay completeness,
  diagnostics removed): core 28/28, queue/cancel, no fault stage, 89.55 s total, exit code 0.
- `smoke-daily-v8/` — 2026-11-25 current daily smoke on manifest v1.3.3 (LF-normalized
  fingerprints, smoke option gating): core 28/28, queue/cancel, no fault stage,
  96.72 s total, exit code 0.
- `preflight-20261124/` — 2026-11-24 read-only drill preflight evidence: A exits 2 with the
  Router down and starts nothing; B exits 0 against the ready Router; C exits 2 while the
  Router still marked `.16` unhealthy after an injected failed recovery; D exits 0 after manual
  recovery. All stop/start counts are zero.
- `stress-20261124-live/` — 2026-11-24 full stress acceptance: replay 18/18, concurrency 12/12,
  cold-rag 6/6 rounds exit 0; 5852.74 s total, exit code 0.
- `stress-failinject-20261124/` — 2026-11-24 replay-failure injection: `budgets.json` hidden,
  18/18 replay rounds exit 1, index kept all failures, `replay.pass=false`, top-level exit 1;
  `budgets.json` restored to manifest hash.
- `fault-20261124-newpreflight/` — 2026-11-24 new read-only preflight plus one real cycle:
  Router reused (`cache_aware`), removal 6.93 s, first complete `.16` response 6.235 s after
  direct health, request ID `2bcca0eb8a8f437c9632a89837152cc5`, exit code 0.
- `fault-20261124-startfail/` — 2026-11-24 injected start failure after a real stop: start
  returned 1, recovery unconfirmed, drill exit 1; compose file and engine restored manually,
  preflight D then passed.

### Archived outside this Git tree (2026-11-25)

`smoke-daily-v1/` … `v6/`, `smoke-entry-v1/`, `smoke-proxy-v6/`, `smoke-proxy-v10/`,
`smoke-proxy-v12/` and `fault-20261124-1x-r2/` were copied to the internal archive
`.verification/dual-qwen/runs-archive/` with verified source/destination SHA-256
(`INDEX.json`, `README.md`) and then removed from the current tree. Git history still
contains them, and the results report keeps their historical numbers.

To re-check an evidence tree on this machine:

```bash
sha256sum -c benchmarks/router/runs/retest-v12/evidence-sha256.txt
```

Regenerate summaries without reissuing requests:

```bash
cd benchmarks/router
python analyze_retest.py --input runs/retest-v12 --output /tmp/retest-analysis.json
python analyze_fault.py --input runs/retest-v12/fault --output /tmp/fault-analysis.json
python analyze_smoke.py --input runs/smoke-v1 --output /tmp/smoke-analysis.json
python cold_rag/analyze_cold_rag.py --input runs/cold-rag-v1/runs --output /tmp/cold-rag-analysis.json
```

Analysis entry points write to the requested output path; the daily default is a run-ID directory
under `.verification/dual-qwen/`, not this versioned archive. New registrations use
`register_smoke_evidence.py --run-id <id>` or `cold_rag/register_cold_rag_evidence.py --run-id <id>`.
