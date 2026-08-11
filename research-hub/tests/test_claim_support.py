"""Production claim-support verifier boundaries."""

from contextlib import nullcontext
from types import SimpleNamespace
import unittest

import httpx

from app.claim_support import (
    ClaimVerifierClient, LocalClaimVerifier, MODEL, REVISION, VerifierUnavailable,
)


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class FakeTorch:
    inference_mode = staticmethod(nullcontext)
    softmax = staticmethod(lambda logits, dim: FakeTensor(logits))


class FakeTokenizer:
    def __init__(self):
        self.calls = []

    def __call__(self, premises, hypotheses, **kwargs):
        self.calls.append(kwargs)
        if isinstance(premises, str):
            return {"input_ids": [0] * (513 if "OVER_BUDGET" in premises else 10)}
        return {"input_ids": [[0]] * len(premises)}


class RecordingTokenizer(FakeTokenizer):
    def __init__(self):
        super().__init__()
        self.batches = []

    def __call__(self, premises, hypotheses, **kwargs):
        if not isinstance(premises, str):
            self.batches.append((premises, hypotheses))
        return super().__call__(premises, hypotheses, **kwargs)


def verifier(probabilities):
    subject = LocalClaimVerifier()
    subject.tokenizer = FakeTokenizer()
    subject.model = lambda **_kwargs: SimpleNamespace(logits=probabilities.pop(0))
    subject.torch = FakeTorch()
    return subject


def material(text="claim", refs=None):
    return {"text": text, "evidence_refs": refs or [
        {"evidence_id": "E1", "span": "evidence", "supports": "claim"},
    ]}


class LocalVerifierTests(unittest.TestCase):
    def test_single_evidence_claim_uses_only_the_sealed_check(self):
        subject = verifier([[[.98, .01, .01]]])
        result = subject.verify([material()])[0]
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["reason"])
        self.assertEqual([check["role"] for check in result["checks"]], ["evidence_union"])
        self.assertEqual(result["checks"][0]["entailment"], .98)
        self.assertTrue(all(call.get("truncation") is False for call in subject.tokenizer.calls))

        subject = verifier([[[.96, .03, .01]]])
        self.assertEqual(subject.verify([material()])[0]["reason"], "low_confidence")

    def test_sealed_union_premise_format_is_preserved(self):
        subject = verifier([[[.98, .01, .01]]])
        subject.tokenizer = RecordingTokenizer()
        subject.verify([material(text="claim", refs=[
            {"evidence_id": "E1", "span": "evidence", "supports": "claim"},
        ])])
        self.assertIn((["Evidence 1: evidence"], ["claim"]), subject.tokenizer.batches)

    def test_neutral_and_over_budget_fail_closed(self):
        subject = verifier([[[.01, .98, .01]]])
        self.assertEqual(subject.verify([material()])[0]["reason"], "neutral")

        subject = verifier([[[.98, .01, .01]]])
        self.assertEqual(
            subject.verify([material(refs=[{
                "evidence_id": "E1", "span": "OVER_BUDGET", "supports": "claim",
            }])])[0]["reason"],
            "over_budget",
        )

    def test_unrelated_padding_fails_a_required_link(self):
        scores = [[.98, .01, .01]] * 3
        scores[1] = [.01, .98, .01]
        subject = verifier([scores])
        result = subject.verify([material(refs=[
            {"evidence_id": "E1", "span": "needed", "supports": "claim"},
            {"evidence_id": "E2", "span": "padding", "supports": "claim"},
        ])])
        self.assertEqual(result[0]["reason"], "neutral")
        self.assertEqual(
            [check["role"] for check in result[0]["checks"]],
            ["span_support", "span_support", "evidence_union"],
        )

    def test_supports_must_restate_the_claim_verbatim(self):
        subject = verifier([[[.98, .01, .01]]])
        result = subject.verify([material(refs=[
            {"evidence_id": "E1", "span": "evidence", "supports": "a different proposition"},
        ])])
        self.assertEqual(result[0]["reason"], "malformed_claim")
        self.assertEqual(result[0]["checks"], [])

    def test_malformed_scores_fail_closed(self):
        subject = verifier([[[.5, .5]]])
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            subject.verify([material()])


class VerifierClientTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, handler):
        subject = ClaimVerifierClient("http://verifier", timeout=.01)
        await subject._client.aclose()
        subject._client = httpx.AsyncClient(
            base_url="http://verifier", transport=httpx.MockTransport(handler), timeout=.01
        )
        self.addAsyncCleanup(subject.close)
        return subject

    async def test_valid_response(self):
        subject = await self.client(lambda _request: httpx.Response(200, json={
            "model": MODEL, "revision": REVISION,
            "results": [{"accepted": True, "reason": None}],
        }))
        self.assertEqual(await subject.verify([material()]), [None])

    async def test_unavailable_timeout_wrong_revision_and_malformed_fail_closed(self):
        cases = (
            (lambda _request: httpx.Response(503), "unavailable"),
            (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("late", request=request)), "timeout"),
            (lambda _request: httpx.Response(200, json={
                "model": MODEL, "revision": "0" * 40,
                "results": [{"accepted": True, "reason": None}],
            }), "revision_mismatch"),
            (lambda _request: httpx.Response(200, json={
                "model": MODEL, "revision": REVISION, "results": [{"accepted": "yes"}],
            }), "malformed_output"),
        )
        for handler, reason in cases:
            with self.subTest(reason=reason):
                subject = await self.client(handler)
                with self.assertRaises(VerifierUnavailable) as raised:
                    await subject.verify([material()])
                self.assertEqual(raised.exception.reason, reason)


if __name__ == "__main__":
    unittest.main()
