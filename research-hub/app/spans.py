"""Deterministic propositional span selection for exact-evidence claims.

Every span returned is an exact contiguous substring of the sanitized chunk it
came from, so `span in chunk_text` remains the resolution invariant.

The attempt-10 packed prompt offered 46 spans, of which 26 were reference-list
debris (`et al.`, `Nat.`, `Med.`, bare initials) and two were unusable for
verification: a chunk-initial sentence fragment and a sentence whose subject was
an unresolved demonstrative. Claims drawn from those spans cannot be entailed by
them, so they are removed before generation rather than rejected after it.
"""

from __future__ import annotations

import re


MIN_SPAN_WORDS = 8
MAX_SPAN_CHARS = 480

# Tokens that end in a period without ending a sentence. Bare initials ("S.",
# "K.") are handled separately by length.
ABBREVIATIONS = frozenset({
    "al", "am", "approx", "biol", "br", "cf", "clin", "dr", "ed", "eds", "eg",
    "engl", "epidemiol", "eq", "et", "etc", "eur", "fig", "figs", "ie",
    "inc", "inform", "int", "j", "jr", "ltd", "med", "nat", "no", "nos", "pp",
    "prof", "ref", "refs", "res", "rev", "sci", "sr", "st", "vol", "vs",
})

_BREAK = re.compile(r"(?P<nl>\n+)|(?<=[.!?])(?P<sp>[ \t]+)")
_TRAILING_TOKEN = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")

# Only demonstratives. Sentence-initial "There"/"It" are usually expletive, not
# referential, and dropping them would discard sound evidence.
_ANAPHORIC = re.compile(r"^(this|that|these|those|such)\b", re.IGNORECASE)

_NON_PROPOSITIONAL = (
    re.compile(r"https?://|doi\.org|\bdoi:", re.IGNORECASE),
    re.compile(r"\bet\s+al\b", re.IGNORECASE),
    re.compile(r"\(\s*\d{4}\s*\)"),
    re.compile(r"\b\d+\s*[–—-]\s*\d+\b"),
    re.compile(r'"\)'),
    re.compile(r"\|"),
)


def _ends_with_abbreviation(text: str) -> bool:
    match = _TRAILING_TOKEN.search(text.rstrip())
    if not match:
        return False
    token = match.group(1).replace(".", "").casefold()
    return len(token) == 1 or token in ABBREVIATIONS


def sentence_bounds(text: str) -> list[tuple[int, int]]:
    """Offsets of sentence-like units; abbreviations never end a sentence."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for match in _BREAK.finditer(text):
        if match.lastgroup == "sp" and _ends_with_abbreviation(text[start:match.start()]):
            continue
        if text[start:match.start()].strip():
            bounds.append((start, match.start()))
        start = match.end()
    if text[start:].strip():
        bounds.append((start, len(text)))
    return bounds


def is_propositional(body: str) -> bool:
    """A self-contained statement a claim can be compressed from."""
    if not MIN_SPAN_WORDS <= len(body.split()) or len(body) > MAX_SPAN_CHARS:
        return False
    if not body[:1].isupper() or body[-1:] not in {".", "!", "?"}:
        return False
    return not any(pattern.search(body) for pattern in _NON_PROPOSITIONAL)


def propositional_spans(text: str) -> list[str]:
    """Exact substrings of `text` that can stand alone as verifier premises."""
    bounds = sentence_bounds(text)
    spans: list[str] = []
    for index, (start, end) in enumerate(bounds):
        body = text[start:end].strip()
        if _ANAPHORIC.match(body):
            if index == 0:
                # The referent is in a neighbouring chunk; resolving it would mean
                # mixing unsanitized document text into the premise.
                continue
            start = bounds[index - 1][0]
            body = text[start:end].strip()
        if is_propositional(body):
            spans.append(body)
    return list(dict.fromkeys(spans))
