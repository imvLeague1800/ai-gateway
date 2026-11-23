"""Observe failure, detection, rejoin and application behavior independently."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import subprocess
import time
import uuid

import httpx

from prompts import qa_specs
from transport import ENGINES, router_snapshot, send_one


def worker_metric(sample, name, worker):
    return next((value for labels, value in sample.get(name, {}).items()
                 if json.loads(labels).get("worker") == worker), None)


def docker(host, action):
    subprocess.run(["ssh", "-o", "BatchMode=yes", f"dgx@{host}", "docker", action, "spark-vllm-qwen36"],
                   check=True, timeout=45, capture_output=True)


async def trial(args, index):
    worker = ENGINES[args.worker]
    host = httpx.URL(worker).host
    run_id = f"fault-{args.policy}-{args.worker}-{index}-{uuid.uuid4().hex[:8]}"
    output = args.output / run_id
    output.mkdir(parents=True)
    clock = time.perf_counter
    origin = clock()
    health_metric = "vllm_router_worker_health"
    processed_metric = "vllm_router_processed_requests_total"
    running_metric = "vllm_router_running_requests"
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        with (output / "timeline.jsonl").open("w", encoding="utf-8") as log:
            def record(event, **fields):
                print(json.dumps(dict(event=event, seconds=clock() - origin, **fields)), file=log, flush=True)

            async def probe(spec, phase, attribute=False):
                before = await router_snapshot(client) if attribute else {}
                result = await send_one(client, spec)
                if attribute:
                    after = await router_snapshot(client)
                    result["dispatch_delta"] = {
                        url: (worker_metric(after, processed_metric, url) or 0)
                        - (worker_metric(before, processed_metric, url) or 0)
                        for url in ENGINES.values()
                    }
                record("probe", phase=phase, result=result)
                return result

            async def worker_health():
                try:
                    response = await client.get(worker + "/health", timeout=0.8)
                    return response.status_code == 200
                except httpx.HTTPError:
                    return False

            async def observe(desc, expected, signal, deadline):
                direct = router = None
                while clock() < deadline:
                    healthy, metrics = await asyncio.gather(worker_health(), router_snapshot(client))
                    marked = worker_metric(metrics, health_metric, worker)
                    record("health", phase=desc, engine=healthy, router=marked)
                    if direct is None and healthy == expected:
                        direct = clock()
                    if router is None and marked == int(expected):
                        router = clock()
                        if signal:
                            signal.set()
                    if direct is not None and router is not None:
                        return direct, router
                    await asyncio.sleep(0.4)
                return direct, router

            snapshot = await router_snapshot(client)
            if any(worker_metric(snapshot, health_metric, url) != 1 for url in ENGINES.values()):
                raise RuntimeError("Fault trial requires two healthy workers")

            long_spec = {**qa_specs(1, run_id)[0], "quality": r"\d",
                         "messages": [dict(role="user", content="Write 150 numbered checklist items, one per line.")]}
            streams = [asyncio.create_task(probe(long_spec, "inflight")) for _ in range(4)]
            await asyncio.sleep(0.8)
            record("before_stop_router", snapshot=await router_snapshot(client))
            stopped = asyncio.Event()
            window = []

            async def window_probes():
                for spec in qa_specs(40, run_id + "-window"):
                    if stopped.is_set():
                        break
                    window.append(asyncio.create_task(probe(spec, "detection_window")))
                    await asyncio.sleep(0.5)

            stop_action = clock()
            record("stop_issued", host=host)
            observing = asyncio.create_task(observe("detection", False, stopped, stop_action + 45))
            probing = asyncio.create_task(window_probes())
            try:
                await asyncio.to_thread(docker, host, "stop")
                record("stop_returned")
                failed, removed = await observing
                stopped.set()
                await probing
                await asyncio.gather(*streams, *window)
                after_remove = []
                removed_worker_offline = False
                if removed is not None:
                    down_before = not await worker_health()
                    for spec in qa_specs(20, run_id + "-removed"):
                        after_remove.append(await probe(spec, "removed", attribute=True))
                    removed_worker_offline = down_before and not await worker_health()
                    record("removed_worker_health_bracket", offline=removed_worker_offline)
            finally:
                stopped.set()
                record("start_issued", host=host)
                await asyncio.to_thread(docker, host, "start")
                record("start_returned")

            engine_back, router_back = await observe("recovery", True, None, clock() + 600)
            accepted = None
            rejoined = []
            if router_back is not None:
                for spec in qa_specs(20, run_id + "-recovered"):
                    row = await probe(spec, "recovered", attribute=True)
                    rejoined.append(row)
                    if accepted is None and row["state"] == "success" and row["dispatch_delta"].get(worker) == 1:
                        accepted = row["started"]

            recovered_dispatches = {url: sum(row["dispatch_delta"].get(url) or 0 for row in rejoined)
                                    for url in ENGINES.values()}
            if router_back is not None and any(count == 0 for count in recovered_dispatches.values()):
                before = await router_snapshot(client)
                burst = await asyncio.gather(*(probe(spec, "recovered_burst")
                                               for spec in qa_specs(8, run_id + "-burst")))
                after = await router_snapshot(client)
                delta = {url: (worker_metric(after, processed_metric, url) or 0)
                         - (worker_metric(before, processed_metric, url) or 0) for url in ENGINES.values()}
                record("recovered_burst_dispatches", dispatch_delta=delta)
                if all(row["state"] == "success" for row in burst):
                    for url, count in delta.items():
                        recovered_dispatches[url] += count
                    if accepted is None and delta[worker] > 0:
                        accepted = max(row["ended"] for row in burst)

            cancel_task = asyncio.create_task(send_one(client, long_spec))
            await asyncio.sleep(0.5)
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            for _ in range(20):
                final = await router_snapshot(client)
                running = {url: worker_metric(final, running_metric, url) for url in ENGINES.values()}
                if all(value == 0 for value in running.values()):
                    break
                await asyncio.sleep(0.5)

    survivor = next(url for url in ENGINES.values() if url != worker)
    result = dict(run_id=run_id, policy=args.policy, worker=args.worker, trial=index,
                  first_direct_failure_s=failed - stop_action if failed else None,
                  removed_s=removed - stop_action if removed else None,
                  router_healthy_after_engine_s=router_back - engine_back if router_back and engine_back else None,
                  accepted_after_engine_s=accepted - engine_back if accepted and engine_back else None,
                  removed_worker_offline=removed_worker_offline,
                  removed_probe_success=sum(
                      row["state"] == "success" and row["dispatch_delta"].get(worker) == 0
                      and (row["dispatch_delta"].get(survivor) == 1
                           or (removed_worker_offline and row["dispatch_delta"].get(survivor) == 0))
                      for row in after_remove),
                  detection_window_states=Counter(task.result()["state"] for task in window),
                  inflight_states=Counter(task.result()["state"] for task in streams),
                  recovered_dispatches=recovered_dispatches,
                  inflight_after_cancel=running)
    (output / "summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


async def run(args):
    for index in range(1, args.repeat + 1):
        await trial(args, index)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True, choices=["power_of_two", "cache_aware"])
    parser.add_argument("--worker", required=True, choices=ENGINES)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path(".verification/dual-qwen/retest"))
    asyncio.run(run(parser.parse_args()))
