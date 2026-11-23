"""Run a frozen profile against an isolated router, and retain raw evidence."""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time
import uuid

import httpx

from generate import BASE, MARKER, SEED
from prompts import qa_specs
from transport import ENGINES, ROUTER_API, engine_snapshot, router_snapshot, send_one

HITS = "vllm:prefix_cache_hits_total"
QUERIES = "vllm:prefix_cache_queries_total"
WAITING = "vllm:num_requests_waiting"


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def load():
    manifest = json.loads((BASE / "manifest.json").read_bytes())
    for name, expected in manifest["sha256"].items():
        if hashlib.sha256((BASE / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"dataset file hash mismatch: {name}")
    cases = {case["id"]: case for case in read_rows(BASE / "cases.jsonl")}
    sessions = read_rows(BASE / "sessions.jsonl")
    if len(cases) != manifest["cases"] or len(sessions) != manifest["sessions"]:
        raise ValueError("unexpected case or session count")
    for session in sessions:
        seen = set()
        for step in session["steps"]:
            if step["case"] not in cases or (step["parent"] is not None and step["parent"] not in seen):
                raise ValueError(f"invalid session dependency: {session['id']}")
            seen.add(step["case"])
    return cases, sessions, json.loads((BASE / "profiles.json").read_bytes())


def percentile(values, p):
    values = sorted(values)
    return values[round(p * (len(values) - 1))] if values else None


def cache_ratio(samples):
    hits = queries = 0
    intervals = 0
    for before, after in zip(samples, samples[1:]):
        for worker in ENGINES:
            old, new = before["engines"][worker], after["engines"][worker]
            if all(metric in old and metric in new and new[metric] >= old[metric] for metric in (HITS, QUERIES)):
                hits += new[HITS] - old[HITS]
                queries += new[QUERIES] - old[QUERIES]
                intervals += 1
    return {"hits": hits, "queries": queries, "ratio": hits / queries if queries else None,
            "intervals": intervals, "expected_intervals": 2 * max(len(samples) - 1, 0)}


def summarize(rows, start, window):
    qualified = [r for r in rows if r["state"] == "success" and r["ttft_ms"] <= r["budget_ms"]]
    accepted = [r for r in qualified if r["started"] < start + window]
    complete = [r for r in qualified if r["completion_budget_ms"] is not None
                and r["complete_ms"] <= r["completion_budget_ms"]]
    result = dict(requests=len(rows), inside_window=sum(r["started"] < start + window for r in rows),
                  states=Counter(r["state"] for r in rows), ttft_qualified=len(qualified),
                  ttft_qualified_rps=len(accepted) / window,
                  complete_qualified=len(complete) if all(r["completion_budget_ms"] is not None for r in rows) else None,
                  complete_qualified_rps=sum(r["started"] < start + window for r in complete) / window
                  if all(r["completion_budget_ms"] is not None for r in rows) else None)
    for metric in ("ttft_ms", "complete_ms"):
        good = [r[metric] for r in rows if r["state"] == "success"]
        result[metric] = {f"p{p}": percentile(good, p / 100) for p in (50, 95, 99)}
    return result


def dispatches(sample):
    return {json.loads(labels)["worker"]: count for labels, count
            in sample.get("vllm_router_processed_requests_total", {}).items()}


async def snapshot(client):
    engines, router = await asyncio.gather(engine_snapshot(client), router_snapshot(client))
    return dict(at=time.perf_counter(), engines=engines, router=router)


async def run(args):
    cases, sessions, profiles = load()
    budgets = json.loads(args.budgets.read_bytes()) if args.budgets else {}
    if args.profile == "stable":
        sessions = [s for kind, count in profiles["stable_sessions"].items()
                    for s in [s for s in sessions if s["id"].startswith(kind)][:count]]
    if args.profile == "calibration":
        probe_cases = qa_specs(12, MARKER + "-calibration")
        for index, case in enumerate(probe_cases):
            value = 19 * (31 + index)
            case.update(messages=[{"role": "user", "content":
                                   f"Marker {MARKER}-{index}. What is 19*{31 + index}? Reply with the number only."}],
                        quality=rf"\b{value}\b")
            cases[case["id"]] = case
        selected = [s for s in sessions if s["id"].startswith(("agent-", "rag-"))][-12:]
        # Separate task variants with a different code or synthetic tool-history result.
        sessions = [s for s in sessions if s["id"].startswith("agent-")][-12:] + selected
        for session in sessions:
            for step in session["steps"]:
                case = cases[step["case"]]
                if case["kind"].startswith("rag"):
                    code = "CAL-" + case["quality"]
                    case["messages"] = json.loads(json.dumps(case["messages"]).replace(case["quality"], code))
                    case["quality"] = code
                else:
                    case["messages"][0]["content"] += "\nCalibration project: canary deployments."
        sessions += [dict(id=case["id"], steps=[dict(case=case["id"], parent=None, gap_s=0)])
                     for case in probe_cases]
    rng = random.Random(SEED + args.group)
    rng.shuffle(sessions)
    rate = profiles["rates"][args.profile]
    namespace = args.namespace or uuid.uuid4().hex[:12]
    run_id = f"{args.profile}-{args.group}-{args.policy}-{uuid.uuid4().hex[:8]}"
    out = args.output / run_id
    out.mkdir(parents=True)
    window = sum(len(s["steps"]) for s in sessions) / rate + profiles["window_tail_s"]
    metadata = dict(run_id=run_id, profile=args.profile, policy=args.policy, group=args.group,
                    namespace=namespace, rate=rate, window_s=window, router=ROUTER_API, engines=ENGINES,
                    git=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    manifest_sha256=hashlib.sha256((BASE / "manifest.json").read_bytes()).hexdigest(),
                    completion_budgets_ms=budgets, unix_start=time.time())
    (out / "run.json").write_text(json.dumps(metadata, indent=2))
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        for i, case in enumerate(list(cases.values())[:5]):
            result = await send_one(client, {**case, "messages": [{"role": "user", "content":
                                                                      f"Warmup {namespace} {i}. " + case["messages"][0]["content"]}]})
            if result["state"] != "success":
                raise RuntimeError(f"warmup failed: {result}")
        baseline = [await snapshot(client)]
        await asyncio.sleep(1)
        baseline.append(await snapshot(client))
        start = time.perf_counter() + 1
        position = 0
        samples = [baseline[-1]]
        rows = []
        stop = asyncio.Event()
        with (out / "metrics.jsonl").open("w", encoding="utf-8") as metrics, (
            out / "requests.jsonl"
        ).open("w", encoding="utf-8") as requests:
            for sample in baseline:
                print(json.dumps(sample), file=metrics, flush=True)

            async def sample_metrics():
                next_at = start
                while not stop.is_set():
                    await asyncio.sleep(max(0, next_at - time.perf_counter()))
                    sample = await snapshot(client)
                    samples.append(sample)
                    print(json.dumps(sample), file=metrics, flush=True)
                    next_at += 1

            async def session_requests(session, offset):
                await asyncio.sleep(max(0, start + offset - time.perf_counter()))
                tasks = {}

                async def send_step(step):
                    planned = start + offset
                    if step["parent"] is not None:
                        parent = await tasks[step["parent"]]
                        planned = parent["ended"] + step["gap_s"]
                        await asyncio.sleep(max(0, planned - time.perf_counter()))
                    case = cases[step["case"]]
                    spec = {**case, "messages": json.loads(json.dumps(case["messages"]).replace(MARKER, namespace))}
                    before = dispatches(await router_snapshot(client)) if args.profile == "stable" else {}
                    header_sample = None

                    def capture_dispatch():
                        nonlocal header_sample
                        header_sample = asyncio.create_task(router_snapshot(client))

                    row = await send_one(client, spec, on_headers=capture_dispatch if args.profile == "stable" else None)
                    if header_sample is not None and before:
                        after = dispatches(await header_sample)
                        deltas = {worker: value - before.get(worker, 0) for worker, value in after.items()}
                        if row["status"] == 200 and sum(deltas.values()) == 1:
                            row["actual_worker"] = next(worker for worker, delta in deltas.items() if delta == 1)
                    row.update(session=session["id"], parent=step["parent"], planned=planned,
                               messages=spec["messages"], tools=spec["tools"],
                               completion_budget_ms=budgets.get(case["kind"]))
                    rows.append(row)
                    print(json.dumps(row), file=requests, flush=True)
                    return row

                for step in session["steps"]:
                    tasks[step["case"]] = asyncio.create_task(send_step(step))
                await asyncio.gather(*tasks.values())

            sampler = asyncio.create_task(sample_metrics())
            try:
                tasks = []
                for session in sessions:
                    tasks.append(asyncio.create_task(session_requests(session, position / rate)))
                    position += len(session["steps"])
                await asyncio.gather(*tasks)
                await asyncio.sleep(max(0, start + window - time.perf_counter()))
            finally:
                stop.set()
                await sampler
    result = summarize(rows, start, window)
    result.update(cache=cache_ratio(samples), start_monotonic=start,
                  drain_s=max(0, max(r["ended"] for r in rows) - start - window),
                  peak_waiting=max(sum(e.get(WAITING, 0) for e in s["engines"].values()) for s in samples),
                  cohorts={kind: summarize([r for r in rows if r["kind"] == kind], start, window)
                           for kind in sorted({r["kind"] for r in rows})})
    indexed = {row["id"]: row for row in rows}
    continuations = [row for row in rows if row["parent"]]
    attributable = [row for row in continuations if row.get("actual_worker")
                    and indexed[row["parent"]].get("actual_worker")]
    result["affinity"] = dict(continuations=len(continuations), attributed=len(attributable),
                              same_worker=sum(row["actual_worker"] == indexed[row["parent"]]["actual_worker"]
                                              for row in attributable))
    (out / "summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"directory": str(out), **result}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=["validate", "calibration", "high", "light", "stable"])
    parser.add_argument("--policy", choices=["power_of_two", "cache_aware"])
    parser.add_argument("--group", type=int, default=1)
    parser.add_argument("--namespace")
    parser.add_argument("--budgets", type=Path)
    parser.add_argument("--output", type=Path, default=Path(os.getenv("BENCH_OUTPUT", ".verification/dual-qwen/retest")))
    args = parser.parse_args()
    if args.profile == "validate":
        cases, sessions, _ = load()
        print(f"Validated {len(cases)} cases and {len(sessions)} sessions")
    elif not args.policy:
        parser.error("--policy is required for replay")
    else:
        asyncio.run(run(args))
