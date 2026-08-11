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
    def test_entailment_threshold_and_no_truncation(self):
        subject = verifier([[[.98, .01, .01]] * 3])
        self.assertEqual(subject.verify([material()]), [{"accepted": True, "reason": None}])
        self.assertTrue(all(call.get("truncation") is False for call in subject.tokenizer.calls))

        subject = verifier([[[.96, .03, .01]] * 3])
        self.assertEqual(subject.verify([material()])[0]["reason"], "low_confidence")

    def test_neutral_and_over_budget_fail_closed(self):
        subject = verifier([[[.01, .98, .01]] * 3])
        self.assertEqual(subject.verify([material()])[0]["reason"], "neutral")

        subject = verifier([[[.98, .01, .01]]])
        self.assertEqual(
            subject.verify([material(refs=[{
                "evidence_id": "E1", "span": "OVER_BUDGET", "supports": "claim",
            }])])[0]["reason"],
            "over_budget",
        )

    def test_unrelated_padding_fails_a_required_link(self):
        scores = [[.98, .01, .01]] * 5
        scores[2] = [.01, .98, .01]
        subject = verifier([scores])
        result = subject.verify([material(refs=[
            {"evidence_id": "E1", "span": "needed", "supports": "claim"},
            {"evidence_id": "E2", "span": "padding", "supports": "unrelated"},
        ])])
        self.assertEqual(result[0]["reason"], "neutral")

    def test_malformed_scores_fail_closed(self):
        subject = verifier([[[.5, .5]] * 3])
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
