"""Create a disjoint calibration partition and freeze completion budgets."""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import httpx

from prompts import FIXED_TOOL_CALL, TOOL_MESSAGE, TOOLS, build_agent_prefix, filler, qa_specs
import transport

BASE = Path(__file__).resolve().parent
SEED = 20261123


def build_cases() -> list[dict]:
    cases = []
    for index, case in enumerate(qa_specs(12, "CAL")):
        case["id"] = f"cal-qa-{index:03}"
        case["source"] = "synthetic-calibration"
        cases.append(case)
    for index in range(12):
        system, user, followup = build_agent_prefix(f"CAL-agent-{index:03}")
        cases.append(dict(id=f"cal-agent-{index:03}-cold", kind="agent_cold", messages=[system, user], tools=TOOLS,
                          quality="tool", quality_type="tool", budget_ms=10000, source="synthetic-calibration"))
        cases.append(dict(id=f"cal-agent-{index:03}-warm", kind="agent_warm",
                          messages=[system, user, FIXED_TOOL_CALL, TOOL_MESSAGE, followup], tools=TOOLS,
                          quality=r"0\.07", quality_type="regex_version", budget_ms=5000, source="synthetic-calibration"))
    for index in range(12):
        code = f"CAL-RAG-{index:03}-AAAAAA"
        salt = f"CAL-rag-{index:03}"
        document = "\n".join(filler(i) for i in range(570))
        split = document.index("\n", 5000)
        recall = f"\nRecall block: the maintenance access code for this archive is {code}.\n"
        messages = [
            dict(role="system", content="You are a precise retrieval assistant. Answer only from the document."),
            dict(role="user", content=f"Batch: {salt}.\nDocument:\n{document[:split]}{recall}{document[split:]}"
                 "\n\nQuestion: What is the maintenance access code? Reply with the code only."),
        ]
        followup = [dict(role="assistant", content=code),
                    dict(role="user", content="Repeat the maintenance access code. Reply with the code only.")]
        cases.append(dict(id=f"cal-rag-{index:03}-cold", kind="rag_cold", messages=messages, tools=None,
                          quality=code, quality_type="code", budget_ms=10000, source="synthetic-calibration"))
        cases.append(dict(id=f"cal-rag-{index:03}-warm", kind="rag_warm", messages=messages + followup, tools=None,
                          quality=code, quality_type="code", budget_ms=5000, source="synthetic-calibration"))
    return cases


def sessions_for(cases: list[dict]) -> list[dict]:
    sessions = []
    for case in cases:
        if case["kind"] == "qa":
            sessions.append({"id": case["id"], "steps": [{"case": case["id"], "parent": None, "gap_s": 0}]})
        elif case["id"].endswith("-cold"):
            base = case["id"].removesuffix("-cold")
            sessions.append({"id": base, "steps": [{"case": case["id"], "parent": None, "gap_s": 0},
                                                   {"case": base + "-warm", "parent": case["id"], "gap_s": 0.5}]})
    return sessions


async def calibrate(cases: list[dict]) -> dict:
    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=1800, write=120, pool=10), trust_env=False) as client:
        for case in cases:
            result = await transport.send_one(client, case, router_api="http://127.0.0.1:18100")
            results.append({"id": case["id"], "kind": case["kind"], "state": result["state"], "complete_ms": result["complete_ms"],
                            "ttft_ms": result["ttft_ms"], "quality": result["quality"]})
    budgets = {}
    for kind in ("qa", "agent_cold", "agent_warm", "rag_cold", "rag_warm"):
        values = sorted(r["complete_ms"] for r in results if r["kind"] == kind and r["state"] == "success")
        if not values:
            budgets[kind] = 30000
            continue
        p95 = values[round(0.95 * (len(values) - 1))]
        floor = {"qa": 2000, "agent_cold": 5000, "agent_warm": 5000, "rag_cold": 10000, "rag_warm": 5000}[kind]
        budgets[kind] = max(floor, int(math.ceil(p95 * 1.5 / 1000.0) * 1000))
    return {"results": results, "budgets": budgets}


def main() -> None:
    cases = build_cases()
    sessions = sessions_for(cases)
    for name, rows in (("calibration_cases.jsonl", cases), ("calibration_sessions.jsonl", sessions)):
        with (BASE / name).open("wb") as out:
            for row in rows:
                out.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
    calibration = asyncio.run(calibrate(cases))
    (BASE / "calibration-results.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")
    (BASE / "budgets.json").write_text(json.dumps(calibration["budgets"], indent=2), encoding="utf-8")
    print(json.dumps(calibration["budgets"], indent=2))


if __name__ == "__main__":
    main()
