"""Synthetic prompts imported from the 2026-11-20 benchmark."""
import json

TOOL_RESULT = json.dumps(
    {"service": "checkout", "status": "degraded", "error_rate": 0.07, "version": "2.4.1"},
    separators=(",", ":"),
)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "Look up the current deployment status for a service.",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    }
]
FIXED_TOOL_CALL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_replay_1",
            "type": "function",
            "function": {"name": "get_service_status", "arguments": '{"service":"checkout"}'},
        }
    ],
}
TOOL_MESSAGE = {
    "role": "tool",
    "tool_call_id": "call_replay_1",
    "name": "get_service_status",
    "content": TOOL_RESULT,
}

def filler(i: int, salt: str = "") -> str:
    return (
        f"Archive record {salt}{i:05d}: the storage cabinet {i % 97} contains routine "
        "inventory notes, labels, and no maintenance credentials or access codes."
    )

def build_agent_prefix(salt: str) -> tuple[dict, dict, dict]:
    system_text = (
        f"Session {salt}. You are an operations assistant. Use tools when asked. "
        "Keep answers short.\nProject context:\n"
        + "\n".join(
            f"Service inventory note {i:05d}: component {i % 83} has routine release notes; "
            "no incident is active unless stated below."
            for i in range(320)
        )
        + "\nThe checkout service is the subject of this session."
    )
    system = {"role": "system", "content": system_text}
    user1 = {
        "role": "user",
        "content": "Look up the deployment status for service checkout using the tool. Do not answer from memory.",
    }
    user2 = {
        "role": "user",
        "content": "What is the error rate and version for checkout? Reply as `error_rate=<number>; version=<version>`.",
    }
    return system, user1, user2

def qa_specs(count: int, tag: str) -> list[dict]:
    questions = [
        ("What is 17*23? Reply with the number only.", r"\b391\b", "qa"),
        ("What is the output of len({1, 2, 2, 3}) in Python? Reply with the number only.", r"\b3\b", "qa"),
        ("Which HTTP status code means Too Many Requests? Reply with the number only.", r"\b429\b", "qa"),
    ]
    specs = []
    for i in range(count):
        question, pattern, kind = questions[i % len(questions)]
        specs.append(
            {
                "id": f"{tag}-qa-{i}",
                "kind": kind,
                "messages": [{"role": "user", "content": f"Marker {tag}-{i}. {question}"}],
                "tools": None,
                "quality": pattern,
                "quality_type": "regex",
                "budget_ms": 2000,
            }
        )
    return specs

