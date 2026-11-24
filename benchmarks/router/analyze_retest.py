"""Pair the retest groups and evaluate the frozen acceptance thresholds."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE.parents[1] / ".verification" / "dual-qwen" / "retest-v12"
INDEX = OUT / "run-index.jsonl"

FAILURE_STATES = {"rejected", "http_error", "timeout", "stream_interrupted"}


def load():
    rows = [json.loads(line) for line in INDEX.read_text(encoding="utf-8").splitlines() if line]
    by = {(r["group"], r["profile"], r["policy"]): r for r in rows if r.get("summary")}
    for row in by.values():
        directory = Path(row["directory"])
        run_path = directory / "run.json"
        row["window_s"] = json.loads(run_path.read_text())["window_s"] if run_path.exists() else None
    return by


def failure_stats(summary):
    states = summary.get("states", {})
    requests = summary["requests"]
    failures = sum(states.get(s, 0) for s in FAILURE_STATES)
    return failures, failures / requests if requests else None


def p95_improvement(p2, ca):
    if not p2 or not ca:
        return None
    return round(1 - ca / p2, 4)


def cohort_p95(summary, kind):
    return summary.get("cohorts", {}).get(kind, {}).get("ttft_ms", {}).get("p95")


def combined_runs(by, profile, policy):
    rows = [r for (g, p, pol), r in by.items() if p == profile and pol == policy]
    return rows


def main():
    by = load()
    result = {"groups": [], "combined": {}, "acceptance": {}}
    for group in (1, 2, 3):
        for profile in ("stable", "high", "light"):
            p2 = by.get((group, profile, "power_of_two"))
            ca = by.get((group, profile, "cache_aware"))
            item = {"group": group, "profile": profile, "power_of_two": p2 and p2["summary"], "cache_aware": ca and ca["summary"]}
            if profile == "stable" and p2 and ca:
                item["rag_warm_p95_improvement"] = p95_improvement(cohort_p95(p2["summary"], "rag_warm"), cohort_p95(ca["summary"], "rag_warm"))
                item["agent_warm_p95_improvement"] = p95_improvement(cohort_p95(p2["summary"], "agent_warm"), cohort_p95(ca["summary"], "agent_warm"))
                item["cache_ratio"] = {"power_of_two": p2["summary"]["cache"]["ratio"], "cache_aware": ca["summary"]["cache"]["ratio"]}
                item["affinity"] = {"power_of_two": p2["summary"].get("affinity"), "cache_aware": ca["summary"].get("affinity")}
                item["pass"] = all(v is not None and v >= 0.15 for v in (item["rag_warm_p95_improvement"], item["agent_warm_p95_improvement"]))
            if profile == "high" and p2 and ca:
                p2f, p2rate = failure_stats(p2["summary"])
                caf, carate = failure_stats(ca["summary"])
                item.update(
                    p99_ratio=round(ca["summary"]["ttft_ms"]["p99"] / p2["summary"]["ttft_ms"]["p99"], 3),
                    complete_rps_ratio=round(ca["summary"]["complete_qualified_rps"] / p2["summary"]["complete_qualified_rps"], 3),
                    failure_rate_delta=round(carate - p2rate, 4),
                    failure_counts={"power_of_two": p2f, "cache_aware": caf},
                    ttft_qualified={"power_of_two": p2["summary"]["ttft_qualified"], "cache_aware": ca["summary"]["ttft_qualified"]},
                    complete_qualified={"power_of_two": p2["summary"]["complete_qualified"], "cache_aware": ca["summary"]["complete_qualified"]},
                    peak_waiting={"power_of_two": p2["summary"]["peak_waiting"], "cache_aware": ca["summary"]["peak_waiting"]},
                    cache_ratio={"power_of_two": p2["summary"]["cache"]["ratio"], "cache_aware": ca["summary"]["cache"]["ratio"]},
                    drain_s={"power_of_two": p2["summary"]["drain_s"], "cache_aware": ca["summary"]["drain_s"]},
                )
                item["pass"] = item["p99_ratio"] <= 1.10 and item["complete_rps_ratio"] >= 0.95 and item["failure_rate_delta"] <= 0.01
            result["groups"].append(item)
    # Combined results from raw requests and per-run windows.
    for profile in ("stable", "high", "light"):
        result["combined"][profile] = {}
        for policy in ("power_of_two", "cache_aware"):
            runs = combined_runs(by, profile, policy)
            requests = []
            states = Counter()
            complete_qualified = 0
            window_s = 0.0
            hits = queries = 0
            for row in runs:
                path = Path(row["directory"]) / "requests.jsonl"
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        rec = json.loads(line)
                        requests.append(rec)
                        states[rec["state"]] += 1
                complete_qualified += row["summary"].get("complete_qualified") or 0
                window_s += row["window_s"] or 0
                hits += row["summary"]["cache"]["hits"]
                queries += row["summary"]["cache"]["queries"]
            ttft = sorted(r["ttft_ms"] for r in requests if r["state"] == "success" and r["ttft_ms"] is not None)

            def pct(values, p):
                return values[round(p * (len(values) - 1))] if values else None

            result["combined"][profile][policy] = {
                "requests": len(requests),
                "states": dict(states),
                "p99": pct(ttft, 0.99),
                "complete_qualified": complete_qualified,
                "window_s": round(window_s, 2),
                "complete_qualified_rps": round(complete_qualified / window_s, 4) if window_s else None,
                "cache_ratio": round(hits / queries, 4) if queries else None,
                "failure_rate": (sum(states.get(s, 0) for s in FAILURE_STATES) / len(requests)) if requests else None,
                "runs": len(runs),
            }
    # Acceptance.
    stable_groups = [g for g in result["groups"] if g["profile"] == "stable" and g.get("pass")]
    high_groups = [g for g in result["groups"] if g["profile"] == "high" and g.get("pass")]
    comb_high = result["combined"]["high"]
    p2h, cah = comb_high["power_of_two"], comb_high["cache_aware"]
    combined_high_pass = (
        cah["p99"] and p2h["p99"] and cah["p99"] / p2h["p99"] <= 1.10
        and cah["complete_qualified_rps"] and p2h["complete_qualified_rps"]
        and cah["complete_qualified_rps"] / p2h["complete_qualified_rps"] >= 0.95
        and cah["failure_rate"] is not None and p2h["failure_rate"] is not None
        and cah["failure_rate"] - p2h["failure_rate"] <= 0.01
    )
    result["acceptance"] = {
        "stable_groups_passed": len(stable_groups),
        "stable_required": len(stable_groups) >= 2,
        "high_groups_passed": len(high_groups),
        "high_required": len(high_groups) >= 2,
        "combined_high_pass": bool(combined_high_pass),
        "combined_high": {
            "p99_ratio": round(cah["p99"] / p2h["p99"], 3) if cah["p99"] and p2h["p99"] else None,
            "complete_rps_ratio": round(cah["complete_qualified_rps"] / p2h["complete_qualified_rps"], 3)
            if cah["complete_qualified_rps"] and p2h["complete_qualified_rps"] else None,
            "failure_rate_delta": round(cah["failure_rate"] - p2h["failure_rate"], 4)
            if cah["failure_rate"] is not None and p2h["failure_rate"] is not None else None,
        },
    }
    (BASE / "retest-analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["acceptance"], indent=2))


if __name__ == "__main__":
    main()
