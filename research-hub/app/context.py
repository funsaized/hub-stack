"""Shared safe-context primitives for untrusted retrieved evidence."""

import re
from collections.abc import Callable
from typing import TypeVar


INJECTION_PATTERNS = (
    re.compile(r"(?i)\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|system)\b.{0,30}\b(instruction|prompt)s?\b"),
    re.compile(r"(?i)\b(system|developer)\s*(message|prompt)\s*:"),
    re.compile(r"(?i)\b(reveal|print|return|exfiltrate)\b.{0,40}\b(secret|token|password|api[ _-]?key|environment)\b"),
    re.compile(r"(?i)\b(call|use|invoke)\b.{0,20}\b(tool|function|shell|terminal)\b"),
)

T = TypeVar("T")


def classify_and_sanitize(text: str) -> tuple[str, list[str]]:
    """Neutralize instruction-like spans without changing retained source text."""
    labels: list[str] = []
    sanitized = text
    for index, pattern in enumerate(INJECTION_PATTERNS, 1):
        if pattern.search(sanitized):
            labels.append(f"prompt_injection_pattern_{index}")
            sanitized = pattern.sub("[potential prompt-injection text removed]", sanitized)
    return sanitized, labels


def token_count(text: str) -> int:
    """Conservative tokenizer-independent upper bound: one token per UTF-8 byte."""
    return len(text.encode("utf-8"))


def render_entry(
    index: int | str, title: str, url: str, text: str,
    document_id: str | None = None,
) -> str:
    document = f"\nDocument ID: {document_id}" if document_id else ""
    return (f'<UNTRUSTED_EVIDENCE id="{index}">\nSource: {title} '
            f'({url}){document}\n{text}\n</UNTRUSTED_EVIDENCE>')


def render_prompt(context: str, question: str) -> str:
    return f"Untrusted evidence:\n{context}\n\nUser question: {question}\n\nAnswer:"


def pack_complete_entries(
    values: list[T], render: Callable[[int, T], str], budget: int,
) -> tuple[list[T], str]:
    """Pack complete entries only, skipping any entry that exceeds the budget."""
    selected: list[T] = []
    entries: list[str] = []
    used = 0
    for value in values:
        entry = render(len(selected) + 1, value)
        cost = token_count(entry) + (2 if entries else 0)
        if used + cost > budget:
            continue
        selected.append(value)
        entries.append(entry)
        used += cost
    return selected, "\n\n".join(entries)
