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


_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(match.group().lower() for match in _WORD.finditer(text))


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap of two token sets; 0.0 when either side is empty."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    return intersection / (len(left) + len(right) - intersection)


def pack_by_marginal_gain(
    values: list[T],
    render: Callable[[int, T], str],
    budget: int,
    *,
    relevance: Callable[[T], float],
    text: Callable[[T], str],
) -> tuple[list[T], str]:
    """Pack by greedy marginal gain: relevance minus redundancy, under budget.

    Rank-order packing spends a fixed budget on the highest-ranked entries
    regardless of how much they repeat each other, so a query whose top
    entries restate one passage buys one passage. This scores each remaining
    entry as ``relevance - lambda * max_overlap(entry, already packed)`` and
    takes the best each round, following the budgeted-selection line of
    arXiv:2607.00725 and the redundancy-aware greedy rule of arXiv:2512.25052.

    Two deliberate departures from that prior art, both conservative:

    - **Redundancy is lexical, not embedding cosine.** The paper's rule
      compares chunk embeddings, but what gets packed here is propositional
      *spans* rewritten out of a chunk, so the stored chunk vector no longer
      describes the packed text and reusing it would measure the wrong thing.
      Token overlap describes exactly the text that will occupy the budget,
      needs no vectors and no network call, and catches the redundancy an
      800/100 overlapping split actually produces. It cannot see paraphrase;
      that is the known cost, recorded rather than hidden.
    - **Lambda is budget pressure, not a tuned constant.** It is the fraction
      of the candidate set the budget cannot admit, so a budget that fits
      everything makes redundancy free and this function degenerates *exactly*
      to rank order. There is no knob to calibrate and no behaviour change
      where there is no scarcity to arbitrate.

    Selection is deterministic: ties resolve to the earlier candidate, so an
    all-equal relevance set packs in the order it arrived.
    """
    if not values:
        return [], ""

    scores = [float(relevance(value)) for value in values]
    low, high = min(scores), max(scores)
    span = high - low
    # Normalised so the penalty is commensurable with relevance whatever
    # scale it arrived on -- an RRF score and a cosine differ by an order of
    # magnitude, and an un-normalised penalty would be inert against one and
    # overwhelming against the other.
    normalised = [1.0 if span <= 0 else (score - low) / span for score in scores]
    tokens = [_tokens(text(value)) for value in values]

    solo_costs = [token_count(render(1, value)) for value in values]
    total = sum(solo_costs)
    penalty_weight = 0.0 if total <= budget else 1.0 - (budget / total)

    selected: list[T] = []
    entries: list[str] = []
    packed_tokens: list[frozenset[str]] = []
    remaining = set(range(len(values)))
    used = 0

    while remaining:
        best_index, best_gain, best_entry, best_cost = None, None, None, None
        for index in sorted(remaining):
            entry = render(len(selected) + 1, values[index])
            cost = token_count(entry) + (2 if entries else 0)
            if used + cost > budget:
                continue
            redundancy = max(
                (_overlap(tokens[index], packed) for packed in packed_tokens),
                default=0.0,
            )
            gain = normalised[index] - penalty_weight * redundancy
            if best_gain is None or gain > best_gain:
                best_index, best_gain, best_entry, best_cost = index, gain, entry, cost
        if best_index is None:
            break
        selected.append(values[best_index])
        entries.append(best_entry)
        packed_tokens.append(tokens[best_index])
        used += best_cost
        remaining.discard(best_index)

    return selected, "\n\n".join(entries)
