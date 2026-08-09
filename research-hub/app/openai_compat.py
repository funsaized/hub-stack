"""Dependency-light OpenAI compatibility helpers."""

import json

CORPUS_MODEL = "research-corpus"


def sse_chunk(
    completion_id: str,
    created: int,
    delta: dict,
    finish_reason: str | None = None,
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": CORPUS_MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
