"""Compare cache-aware rerouting with a probe pinned through a separate router."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid

import httpx

from fault import worker_metric
from generate import MARKER
from replay import load
from transport import ENGINES, ROUTER_API, engine_snapshot, router_snapshot, send_one

PROCESSED = "vllm_router_processed_requests_total"
RUNNING = "vllm_router_running_requests"


def by_worker(metrics, name):
    return {worker: worker_metric(metrics, name, worker) or 0 for worker in ENGINES.values()}


async def measured(client, spec):
    initial = by_worker(await router_snapshot(client), PROCESSED)
    row = await send_one(client, spec)
    final = by_worker(await router_snapshot(client), PROCESSED)
    workers = [worker for worker in final if final[worker] - initial[worker] == 1]
    row["actual_worker"] = workers[0] if len(workers) == 1 else None
    return row


def long_request(spec):
    return {**spec, "messages": [*spec["messages"][:-1],
                                 {"role": "user", "content":
                                  "Repeat the code and write 100 numbered inventory notes."}]}


async def launch_load(client, spec, tasks):
    for _ in range(3):
        tasks.append(asyncio.create_task(send_one(client, long_request(spec))))
        await asyncio.sleep(0.22)


async def seed(client, cases, index):
    namespace = f"{uuid.uuid4().int % 10**9:09d}"
    specs = [{**cases[f"rag-{index:03}-{phase}"],
              "messages": json.loads(json.dumps(cases[f"rag-{index:03}-{phase}"]["messages"])
                                     .replace(MARKER, namespace))}
             for phase in ("cold", "warm")]
    cold, warm = await measured(client, specs[0]), await measured(client, specs[1])
    return specs[1], cold, warm


def hot_on(cold, warm, worker):
    return (warm["actual_worker"] == worker and cold["actual_worker"] == worker
            and cold["state"] == warm["state"] == "success")


async def setup(client, cases, target):
    for index in range(0, 12, 2):
        spec, cold, warm = await seed(client, cases, index)
        if hot_on(cold, warm, target):
            return spec, cold, warm
        other = next(worker for worker in ENGINES.values() if worker != target)
        if hot_on(cold, warm, other):
            tasks = []
            try:
                await launch_load(client, spec, tasks)
                alternate, alt_cold, alt_warm = await seed(client, cases, index + 1)
            finally:
                await asyncio.gather(*tasks)
            if hot_on(alt_cold, alt_warm, target):
                return alternate, alt_cold, alt_warm
    raise RuntimeError(f"Could not establish a hot prefix on {target}")


async def compare(client, spec, target, pinned, forced):
    tasks = []
    try:
        await launch_load(client, spec, tasks)
        for _ in range(20):
            counts = by_worker(await router_snapshot(client), RUNNING)
            if counts[target] == 3 and sum(counts.values()) == 3:
                break
            await asyncio.sleep(0.1)
        initial = by_worker(await router_snapshot(client), PROCESSED)
        engines = await engine_snapshot(client)
        probe = await send_one(client, spec, router_api=pinned if forced else ROUTER_API)
        final = by_worker(await router_snapshot(client), PROCESSED)
        dispatched = {worker: final[worker] - initial[worker] for worker in final}
        if forced:
            probe["actual_worker"] = target if probe["state"] == "success" else None
        else:
            workers = [worker for worker in dispatched if dispatched[worker] == 1]
            probe["actual_worker"] = workers[0] if len(workers) == 1 else None
    finally:
        backgrounds = await asyncio.gather(*tasks)
    return dict(forced=forced, target=target, running_at_probe=counts, engine_metrics_at_probe=engines,
                dispatch_delta=dispatched, probe=probe, backgrounds=backgrounds)


async def main(output, pinned, worker, arm):
    cases, _, _ = load()
    output.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        evidence = []
        for index, (name, target) in enumerate(ENGINES.items()):
            if worker and worker != name:
                continue
            for forced in ((False, True) if index == 0 else (True, False)):
                if arm and forced != (arm == "forced"):
                    continue
                try:
                    spec, cold, warm = await setup(client, cases, target)
                    row = await compare(client, spec, target, pinned[index], forced)
                    row.update(request=spec, cold=cold, warm=warm)
                except RuntimeError as error:
                    row = dict(target=target, forced=forced, error=str(error))
                evidence.append(row)
                output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                print({key: row[key] for key in ("target", "forced", "running_at_probe", "error") if key in row},
                      flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pinned", nargs=2, required=True, help="Single-worker router URLs in WORKER_A/B order")
    parser.add_argument("--worker", choices=ENGINES)
    parser.add_argument("--arm", choices=["automatic", "forced"])
    args = parser.parse_args()
    asyncio.run(main(args.output, args.pinned, args.worker, args.arm))
