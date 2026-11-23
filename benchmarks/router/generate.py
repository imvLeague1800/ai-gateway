"""Frozen synthetic corpus. All assistant/tool history is synthetic."""
import hashlib
import json
from pathlib import Path

from prompts import FIXED_TOOL_CALL, TOOL_MESSAGE, TOOLS, build_agent_prefix, filler, qa_specs

BASE = Path(__file__).resolve().parent
MARKER = "BENCH_NAMESPACE"
SEED = 20261123


def generate():
    cases = qa_specs(90, MARKER)
    for index, case in enumerate(cases):
        case["id"] = f"qa-{index:03}"
    for index in range(60):
        system, user, followup = build_agent_prefix(f"{MARKER}-agent-{index:03}")
        for stage, messages, quality, budget in [
            ("cold", [system, user], "tool", 10000),
            ("warm", [system, user, FIXED_TOOL_CALL, TOOL_MESSAGE, followup], "regex_version", 5000),
        ]:
            cases.append(dict(id=f"agent-{index:03}-{stage}", kind=f"agent_{stage}", messages=messages,
                              tools=TOOLS, quality=quality, quality_type=quality, budget_ms=budget))
    for index in range(45):
        code = f"RAG-{index:03}-AAAAAA"
        salt = f"{MARKER}-rag-{index:03}"
        document = "\n".join(filler(i) for i in range(527))
        recall = f"\nRecall block: the maintenance access code for this archive is {code}.\n"
        split = document.index("\n", 5000)
        messages = [
            dict(role="system", content="You are a precise retrieval assistant. Answer only from the document."),
            dict(role="user", content=f"Batch: {salt}.\nDocument:\n{document[:split]}{recall}{document[split:]}"
                 "\n\nQuestion: What is the maintenance access code? Reply with the code only."),
        ]
        followup = [dict(role="assistant", content=code),
                    dict(role="user", content="Repeat the maintenance access code. Reply with the code only.")]
        for stage, prompt, budget in [("cold", messages, 10000), ("warm", messages + followup, 5000)]:
            cases.append(dict(id=f"rag-{index:03}-{stage}", kind=f"rag_{stage}", messages=prompt,
                              tools=None, quality=code, quality_type="code", budget_ms=budget))
    sessions = []
    for case in cases:
        case["source"] = "synthetic"
        case_id = case["id"]
        if case["kind"] == "qa":
            sessions.append(dict(id=case_id, steps=[dict(case=case_id, parent=None, gap_s=0)]))
        elif case_id.endswith("-cold"):
            session = case_id.removesuffix("-cold")
            sessions.append(dict(id=session, steps=[dict(case=case_id, parent=None, gap_s=0),
                                                   dict(case=session + "-warm", parent=case_id, gap_s=0.5)]))
    for name, rows in [("cases.jsonl", cases), ("sessions.jsonl", sessions)]:
        with (BASE / name).open("wb") as out:
            for row in rows:
                out.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
    assets = ["cases.jsonl", "sessions.jsonl", "profiles.json", "generate.py", "prompts.py"]
    manifest = dict(version="1.0.0", schema_version=1, generator_version=1, seed=SEED,
                    source="2026-11-20 synthetic dual-Qwen benchmark, expanded for retest",
                    purpose="internal routing regression", cases=len(cases), sessions=len(sessions),
                    real_agent_sessions=0,
                    sha256={name: hashlib.sha256((BASE / name).read_bytes()).hexdigest() for name in assets})
    (BASE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate()
