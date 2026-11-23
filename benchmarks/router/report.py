"""Build paired tables from saved replay evidence without reissuing requests."""
import argparse
import json
from pathlib import Path

from replay import WAITING, cache_ratio, percentile, read_rows, summarize

POLICIES = ("power_of_two", "cache_aware")
EXECUTION_FAILED = {"rejected", "http_error", "timeout", "stream_interrupted"}


def fmt(number, digits=3):
    return "-" if number is None else f"{number:.{digits}f}"


def difference(new, old):
    return new / old - 1 if new is not None and old else None


def failure_rate(summary):
    return sum(summary["states"].get(state, 0) for state in EXECUTION_FAILED) / summary["requests"]


def collect(root):
    pairs = {}
    for summary_file in root.glob("*/summary.json"):
        run_file = summary_file.with_name("run.json")
        if not run_file.exists():
            continue
        run = json.loads(run_file.read_bytes())
        if run["profile"] == "calibration":
            continue
        key = (run["profile"], run["group"])
        entries = pairs.setdefault(key, {})
        if run["policy"] in entries:
            raise ValueError(f"multiple runs for {key} {run['policy']}; select a single evidence set")
        entries[run["policy"]] = (run, json.loads(summary_file.read_bytes()),
                                  read_rows(summary_file.with_name("requests.jsonl")))
    return pairs


def aggregate(items):
    combined = []
    offset = 0
    cache_queries = cache_hits = 0
    for run, summary, rows in items:
        start = summary["start_monotonic"]
        # Map each window into a disjoint interval. Keep late sends outside the
        # combined window by placing them beyond the end during aggregation.
        for row in rows:
            rel = row["started"] - start
            combined.append(dict(row, started=offset + rel, inside=rel < run["window_s"]))
        offset += run["window_s"]
        cache_queries += summary["cache"]["queries"]
        cache_hits += summary["cache"]["hits"]
    for row in combined:
        if not row["inside"]:
            row["started"] = offset
    summary = summarize(combined, 0, offset)
    summary["cache"] = {"ratio": cache_hits / cache_queries if cache_queries else None}
    return dict(window_s=offset), summary, combined


def evaluate(profile, baseline, candidate):
    _, left, left_rows = baseline
    _, right, right_rows = candidate
    if profile == "stable":
        warm = lambda rows: percentile([r["ttft_ms"] for r in rows if r["parent"] and r["state"] == "success"], .95)
        change = difference(warm(right_rows), warm(left_rows))
        left_cache, right_cache = left["cache"]["ratio"], right["cache"]["ratio"]
        passed = None if change is None or left_cache is None or right_cache is None else change <= -.15 and right_cache > left_cache
        return change, None, None, passed
    p99 = difference(right["ttft_ms"]["p99"], left["ttft_ms"]["p99"])
    throughput = difference(right["complete_qualified_rps"], left["complete_qualified_rps"])
    failures = failure_rate(right) - failure_rate(left)
    passed = None if None in (p99, throughput) else p99 <= .10 and throughput >= -.05 and failures <= .01
    if profile == "high" and any(entry[1]["requests"] < 300 for entry in (baseline, candidate)):
        passed = None
    return p99, throughput, failures, passed


def render(root):
    pairs = collect(root)
    print("| Profile | Round | Policy | Requests / sent in window | Success | Cache token ratio | TTFT P99 ms | Completion P99 ms | TTFT / full qualified requests | TTFT qualified req/s | Full qualified req/s |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for (profile, group), entries in sorted(pairs.items()):
        for policy, (_, result, _) in entries.items():
            print(f"| {profile} | {group} | {policy} | {result['requests']} / {result['inside_window']} | "
                  f"{result['states'].get('success', 0)} | {fmt(result['cache']['ratio'])} | "
                  f"{fmt(result['ttft_ms']['p99'], 1)} | {fmt(result['complete_ms']['p99'], 1)} | "
                  f"{result['ttft_qualified']} / {result['complete_qualified']} | "
                  f"{fmt(result['ttft_qualified_rps'])} | {fmt(result['complete_qualified_rps'])} |")
    print()
    print("| Profile | Round | TTFT delta (stable warm P95, mixed P99) | Full qualified req/s delta | Execution failure rate delta | Numeric gates |")
    print("| --- | --- | --- | --- | --- | --- |")

    def emit(profile, group, pair):
        p99, throughput, failures, passed = evaluate(profile, *pair)
        print(f"| {profile} | {group} | {fmt(p99)} | {fmt(throughput)} | {fmt(failures)} | "
              f"{'pass' if passed else 'fail' if passed is False else 'unverified'} |")

    for profile in sorted({p for p, _ in pairs}):
        complete = []
        for (p, group), entries in sorted(pairs.items()):
            if p == profile and all(policy in entries for policy in POLICIES):
                pair = tuple(entries[policy] for policy in POLICIES)
                complete.append(pair)
                emit(profile, group, pair)
        if complete:
            emit(profile, "pooled", tuple(aggregate(items) for items in zip(*complete)))


    print()
    print("| Run | Router window arrival req/s | Actual in-window request span req/s | Peak waiting | Waiting by window third (mean) | Drain s | Cache ratio sampled in send window | Prompt tokens / cache queries | Attributed followups (same / attributed / total) |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for _, entries in sorted(pairs.items()):
        for run, result, rows in entries.values():
            start = result["start_monotonic"]
            window = run["window_s"]
            metric_path = root / run["run_id"] / "metrics.jsonl"
            metrics = read_rows(metric_path)
            # Estimate the send-window ratio from complete observations on
            # either side of its interior, excluding post-window drain.
            fixed_cache = cache_ratio([sample for sample in metrics if start <= sample["at"] <= start + window])
            waiting = [[], [], []]
            for sample in metrics:
                elapsed = sample["at"] - start
                if 0 <= elapsed < window:
                    waiting[int(3 * elapsed / window)].append(
                        sum(engine.get(WAITING, 0) for engine in sample["engines"].values()))
            trends = " / ".join(fmt(sum(third) / len(third), 1) if third else "-" for third in waiting)
            starts = [row["started"] for row in rows if start <= row["started"] < start + window]
            arrival = (len(starts) - 1) / (max(starts) - min(starts)) if len(starts) > 1 else None
            tokens = sum(row["usage"].get("prompt_tokens", 0) for row in rows)
            affinity = result.get("affinity", {})
            suffix = f"{affinity.get('same_worker', 0)} / {affinity.get('attributed', 0)} / {sum(bool(r['parent']) for r in rows)}"
            print(f"| {run['run_id']} | {fmt(len(starts) / window)} | {fmt(arrival)} | "
                  f"{result['peak_waiting']:.0f} | {trends} | {fmt(result['drain_s'], 2)} | "
                  f"{fmt(fixed_cache['ratio'])} | {tokens} / {result['cache']['queries']:.0f} | {suffix} |")

    print()
    print("| Profile | Round | Policy | Cohort | Successful / total | TTFT P50 / P95 / P99 ms | Completion P50 / P95 / P99 ms |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for (profile, group), entries in sorted(pairs.items()):
        for policy, (_, result, _) in entries.items():
            for name, cohort in result["cohorts"].items():
                values = [" / ".join(fmt(cohort[metric][f"p{p}"], 1) for p in (50, 95, 99))
                          for metric in ("ttft_ms", "complete_ms")]
                print(f"| {profile} | {group} | {policy} | {name} | {cohort['states'].get('success', 0)} / "
                      f"{cohort['requests']} | {values[0]} | {values[1]} |")


def render_faults(root):
    print("| Policy | Worker | Trial | Direct failure s | Router removal s | Engine startup s | First recovered acceptance s | Successful probes after removal | Detection window states | In-flight states | Recovered dispatches A/B | Running after cancellation A/B |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for path in sorted(root.glob("fault-*/summary.json")):
        summary = json.loads(path.read_bytes())
        timeline = read_rows(path.with_name("timeline.jsonl"))
        events = {item["event"]: item["seconds"] for item in timeline
                  if item["event"] in {"stop_returned", "start_issued"}}
        engine_ready = next((item["seconds"] for item in timeline
                             if item.get("phase") == "recovery" and item.get("engine") is True), None)
        started = events.get("start_issued")
        startup = engine_ready - started if engine_ready is not None and started is not None else None
        removed = [item for item in timeline if item.get("phase") == "removed"]
        # The single-healthy-worker fast path may omit an increment. The
        # successful responses inside the guaranteed Docker stop interval
        # still identify the sole available worker in this two-worker router.
        successful = sum(item["result"]["state"] == "success"
                         and events["stop_returned"] <= item["seconds"] < events["start_issued"]
                         for item in removed)
        states = lambda key: ", ".join(f"{state}={count}" for state, count in summary[key].items()) or "-"
        workers = lambda key: " / ".join(fmt(value, 0) for _, value in sorted(summary[key].items()))
        print(f"| {summary['policy']} | {summary['worker']} | {summary['trial']} | "
              f"{fmt(summary['first_direct_failure_s'], 2)} | {fmt(summary['removed_s'], 2)} | "
              f"{fmt(startup, 2)} | {fmt(summary['accepted_after_engine_s'], 2)} | "
              f"{successful} / {len(removed)} | {states('detection_window_states')} | "
              f"{states('inflight_states')} | {workers('recovered_dispatches')} | "
              f"{workers('inflight_after_cancel')} |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--faults-only", action="store_true")
    args = parser.parse_args()
    if args.faults_only:
        render_faults(args.evidence)
    else:
        render(args.evidence)
