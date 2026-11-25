# Injected start-failure drill — 2026-11-24

Controlled real-failure test of the drill's recovery responsibility: after the drill stopped `.16`
and confirmed the stopped state, a remote watcher moved `/home/dgx/spark-vllm/compose.yaml` aside,
so the recovery `docker compose start qwen` could not run. The file was restored and the engine
started manually immediately after the drill exited, then readiness was re-confirmed.

- Command: `python run_fault_drill.py --cycles 1 --maintenance-window-ack --run-id fault-20261124-startfail --health-timeout 60`
- Preflight passed read-only and the ready Router (`cache_aware`, container `b255f3ba1986`) was reused.
- Stop: `docker compose stop qwen` returned 0 (11.27 s); stop state confirmed (`ps` empty, state `exited`).
- Start attempt: returncode 1, stderr `no configuration file provided: not found` (`start_ok=false`).
- Recovery: no direct health within 60 s (`recovery_error=.16 engine health did not recover within 60.0s`);
  `first_direct_health=null`, `recovery_confirmed=false`, `router_accepts_16=false`.
- Drill result: `pass=false`, `aborted_reason` set, `final_health={"15":200,"16":0}`, exit code 1,
  total 92.23 s. No further cycle started.
- Manual recovery: compose file restored (same 2513-byte file), `docker compose start qwen` returned 0;
  `.16` direct health 200 after 195 s; Router re-accepted `.16` within ~3 s; read-only preflight
  `preflight-20261124-d` then exited 0 with both engines 200.
- Raw evidence: `.verification/dual-qwen/fault-20261124-startfail/summary.json`, `cycle-01.json`.
- Registered copies: `summary.json`, `cycle-01.json`, `evidence-sha256.txt`.
