"""Shared canonical hashing and bounds for the v4 judge-gate evaluation (HUB-036).

Mirrors the v3 multi-ref protocol (`multiref_blind_seal.py`, archived on
`hub-032-cross-source-disagreement`): the draft freeze seals case CONTENT
(id, claim, span texts) so operator annotations can be added without breaking
the seal, while any post-freeze edit to a claim or span fails the fixture test.

v4 differences, per the operator-approved protocol (2026-08-12):

- The gate under evaluation is the MiniMax M3 LLM judge (HUB-035), so the seal
  additionally freezes the judge configuration (system-prompt SHA-256,
  requested model, temperature) and records the served model version observed
  during the final. A served-model change invalidates the seal and requires a
  fresh blind set (cloud re-baseline trigger).
- New strata: metric-name confusion (HUB-033) and adversarial injection.
- Injection evidence is a real corpus span plus a constructed adversarial
  payload; such refs record the exact corpus `base_span` (chunk-bound) and the
  `payload`, and the judged span is their deterministic composition.
"""

from __future__ import annotations

import hashlib
import json

STRATA = {
    "single_entailment": 15,
    "single_neutral": 15,
    "single_contradiction": 10,
    "joint_evidence": 25,
    "padding": 20,
    "cross_source_disagreement": 20,
    "metric_confusion": 10,
    "adversarial_injection": 15,
}
SHUFFLE_SEED = 20260812
CLAIM_MIN, CLAIM_MAX = 20, 240
SPAN_MIN, SPAN_MAX = 80, 350
# An injected span is base_span + "\n" + payload and may run longer.
INJECTED_SPAN_MAX = 700

CALIBRATION_KINDS = {
    "single_entailment_by_design", "single_contradiction_by_design",
    "joint_by_design", "padding_by_design", "neutral_by_design",
    "metric_confusion_by_design", "injection_by_design",
}


def evidence_span(ref: dict) -> str:
    """The exact text judged for one evidence ref.

    Plain refs carry `span` (an exact substring of their source chunk).
    Injection refs carry `base_span` (exact corpus substring) and `payload`
    (constructed adversarial text); the judged span is their composition,
    deterministic so the content seal covers it.
    """
    if "payload" in ref:
        return f'{ref["base_span"]}\n{ref["payload"]}'
    return ref["span"]


def case_content(case: dict) -> dict:
    """The sealed portion of one case: identity, claim text, and span texts."""
    return {
        "id": case["id"],
        "claim": case["claim"],
        "spans": [evidence_span(ref) for ref in case["evidence"]],
    }


def content_sha256(cases: list[dict]) -> str:
    canonical = json.dumps(
        [case_content(case) for case in cases],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def annotations_sha256(cases: list[dict]) -> str:
    """Seal the operator's labels so they cannot silently change after review."""
    canonical = json.dumps(
        [{"id": case["id"], "annotation": case["annotation"]} for case in cases],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def judge_config_fingerprint() -> dict:
    """The frozen judge configuration measured by the v4 final.

    Freezing binds the evaluation to the exact system prompt, requested model,
    and temperature the gate deploys with; the served model version is
    recorded per verdict at run time and re-baselines the gate when it drifts.
    """
    from app.judge_gate import JUDGE_SYSTEM, RESPONSE_MAX_TOKENS

    return {
        "system_prompt_sha256": hashlib.sha256(JUDGE_SYSTEM.encode("utf-8")).hexdigest(),
        "temperature": 0,
        "response_max_tokens": RESPONSE_MAX_TOKENS,
    }


def wire_claim(case: dict) -> dict:
    """Wire one fixture case into the gate's claim contract."""
    return {
        "text": case["claim"],
        "evidence_refs": [{
            "span_id": f"P{index}", "evidence_id": f"E{index}",
            "span": evidence_span(ref), "supports": case["claim"],
        } for index, ref in enumerate(case["evidence"], 1)],
    }
