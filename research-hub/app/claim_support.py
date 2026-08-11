"""Pinned, fail-closed claim-support verification.

The sealed final evaluation measured one NLI pair per case: premise
``"Evidence 1: <span> ..."`` against the claim, at argmax entailment >= 0.97.
`verify` reproduces exactly that pair for single-evidence claims so the sealed
result describes the deployed gate. Changing the model, revision, threshold or
the union premise format invalidates the seal and requires a new blind set.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException


MODEL = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
REVISION = "6f5cf0a2b59cabb106aca4c287eed12e357e90eb"
THRESHOLD = 0.97
MAX_TOKENS = 512
BATCH_SIZE = 8
MAX_EVIDENCE_REFS = 8
LABELS = ("entailment", "neutral", "contradiction")
REJECTION_REASONS = {"neutral", "contradiction", "low_confidence", "over_budget", "malformed_claim"}

logger = logging.getLogger(__name__)


def _bounded_text(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else ""
    return {
        "text": text[:512], "chars": len(text),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "truncated": len(text) > 512,
    }


class VerifierUnavailable(RuntimeError):
    """The pinned verifier could not produce a trustworthy decision."""

    def __init__(self, reason: str):
        super().__init__(f"claim verifier {reason}")
        self.reason = reason


class ClaimVerifierClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health")
            data = response.json()
            return (
                response.is_success
                and data.get("ready") is True
                and data.get("model") == MODEL
                and data.get("revision") == REVISION
            )
        except Exception:
            return False

    async def verify(self, claims: list[dict[str, Any]]) -> list[str | None]:
        try:
            response = await self._client.post("/verify", json={"claims": claims})
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise VerifierUnavailable("timeout") from exc
        except Exception as exc:
            raise VerifierUnavailable("unavailable") from exc
        if data.get("model") != MODEL or data.get("revision") != REVISION:
            raise VerifierUnavailable("revision_mismatch")
        results = data.get("results")
        if not isinstance(results, list) or len(results) != len(claims):
            raise VerifierUnavailable("malformed_output")
        reasons: list[str | None] = []
        for result in results:
            if not isinstance(result, dict) or type(result.get("accepted")) is not bool:
                raise VerifierUnavailable("malformed_output")
            reason = result.get("reason")
            if result["accepted"]:
                if reason is not None:
                    raise VerifierUnavailable("malformed_output")
                reasons.append(None)
            elif reason in REJECTION_REASONS:
                reasons.append(reason)
            else:
                raise VerifierUnavailable("malformed_output")
        logger.info("claim_verification_diagnostic", extra={"diagnostic": {
            "claims": [{
                "text": _bounded_text(claim.get("text")),
                "evidence_refs": [{
                    "span_id": ref.get("span_id"),
                    "span": _bounded_text(ref.get("span")),
                    "supports": _bounded_text(ref.get("supports")),
                } for ref in claim.get("evidence_refs", [])[:MAX_EVIDENCE_REFS]
                if isinstance(ref, dict)],
            } for claim in claims[:10]],
            "results": results[:10],
        }})
        return reasons


class LocalClaimVerifier:
    def load(self) -> None:
        os.environ.update({
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        })
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.manual_seed(0)
        torch.use_deterministic_algorithms(True)
        load = {"revision": REVISION, "local_files_only": True}
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL, **load)
        self.model = AutoModelForSequenceClassification.from_pretrained(MODEL, **load)
        if getattr(self.model.config, "_commit_hash", None) != REVISION:
            raise RuntimeError("claim verifier revision mismatch")
        self.model.eval().to("cpu")
        self.torch = torch

    def verify(self, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pairs: list[tuple[str, str]] = []
        owners: list[int] = []
        roles: list[str] = []
        rejected: dict[int, str] = {}
        for owner, claim in enumerate(claims):
            text = claim.get("text")
            refs = claim.get("evidence_refs")
            if (
                not isinstance(text, str) or not text.strip()
                or not isinstance(refs, list) or not 1 <= len(refs) <= MAX_EVIDENCE_REFS
            ):
                rejected[owner] = "malformed_claim"
                continue
            hypothesis = text.strip()
            spans: list[str] = []
            for ref in refs:
                if not isinstance(ref, dict):
                    rejected[owner] = "malformed_claim"
                    break
                span, supports = ref.get("span"), ref.get("supports")
                # `supports` must restate the claim verbatim. Asserting equality is strictly
                # stronger than the NLI self-check it replaces, and costs no inference.
                if (
                    not isinstance(span, str) or not span.strip()
                    or not isinstance(supports, str) or supports.strip() != hypothesis
                ):
                    rejected[owner] = "malformed_claim"
                    break
                spans.append(span.strip())
            if owner in rejected:
                continue
            # Only `evidence_union` was measured by the sealed final evaluation, so a
            # single-evidence claim is judged by exactly the sealed rule. Multi-evidence
            # claims additionally hold against each span alone, which can only reject
            # more, so the sealed zero-unsupported-acceptance guarantee still holds.
            if len(spans) > 1:
                for span in spans:
                    pairs.append((span, hypothesis))
                    owners.append(owner)
                    roles.append("span_support")
            premise = " ".join(
                f"Evidence {index}: {span}" for index, span in enumerate(spans, 1)
            )
            pairs.append((premise, hypothesis))
            owners.append(owner)
            roles.append("evidence_union")

        runnable: list[tuple[int, str, str]] = []
        checks: dict[int, dict[str, Any]] = {}
        lengths: dict[int, int] = {}
        for index, (premise, hypothesis) in enumerate(pairs):
            length = len(self.tokenizer(premise, hypothesis, truncation=False)["input_ids"])
            lengths[index] = length
            if length > MAX_TOKENS:
                rejected.setdefault(owners[index], "over_budget")
                checks[index] = {
                    "role": roles[index], "tokens": length,
                    "label": "over_budget", "entailment": None,
                }
            else:
                runnable.append((index, premise, hypothesis))

        decisions: dict[int, str | None] = {}
        with self.torch.inference_mode():
            for offset in range(0, len(runnable), BATCH_SIZE):
                batch = runnable[offset:offset + BATCH_SIZE]
                encoded = self.tokenizer(
                    [item[1] for item in batch], [item[2] for item in batch],
                    padding=True, truncation=False, return_tensors="pt",
                )
                logits = self.model(**encoded).logits
                probabilities = self.torch.softmax(logits, dim=-1).detach().cpu().tolist()
                if len(probabilities) != len(batch):
                    raise RuntimeError("claim verifier returned malformed output")
                for item, raw in zip(batch, probabilities):
                    if len(raw) != len(LABELS) or any(not math.isfinite(value) for value in raw):
                        raise RuntimeError("claim verifier returned malformed output")
                    scores = dict(zip(LABELS, raw))
                    predicted = max(scores, key=scores.get)
                    reason = None if predicted == "entailment" and scores["entailment"] >= THRESHOLD else (
                        predicted if predicted != "entailment" else "low_confidence"
                    )
                    checks[item[0]] = {
                        "role": roles[item[0]],
                        "tokens": lengths[item[0]],
                        "label": predicted,
                        "entailment": round(scores["entailment"], 6),
                    }
                    owner = owners[item[0]]
                    if reason:
                        decisions.setdefault(owner, reason)

        return [
            {"accepted": False, "reason": rejected.get(index) or decisions.get(index),
             "checks": [checks[pair] for pair, owner in enumerate(owners)
                        if owner == index and pair in checks]}
            if rejected.get(index) or decisions.get(index)
            else {"accepted": True, "reason": None,
                  "checks": [checks[pair] for pair, owner in enumerate(owners)
                             if owner == index and pair in checks]}
            for index in range(len(claims))
        ]


verifier = LocalClaimVerifier()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(verifier.load)
    yield


app = FastAPI(lifespan=lifespan)
# ponytail: serialize inference to keep the 2.5 GiB envelope; add replicas only if throughput requires it.
verification_lock = asyncio.Lock()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ready": True, "model": MODEL, "revision": REVISION}


@app.post("/verify")
async def verify(payload: dict[str, Any]) -> dict[str, Any]:
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise HTTPException(400, "claims must be a list")
    try:
        async with verification_lock:
            results = await asyncio.to_thread(verifier.verify, claims)
    except Exception as exc:
        raise HTTPException(503, "claim verifier failed closed") from exc
    return {"model": MODEL, "revision": REVISION, "results": results}
