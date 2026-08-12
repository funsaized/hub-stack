"""Deterministic structure and freeze-integrity checks for the v4 judge
evaluation fixtures (HUB-036). Offline: corpus binding (exact substring +
chunk SHA-256) is a construction-time property verified against the document
store by the fixture builders; these tests keep the committed fixtures
well-formed and detect any post-freeze edit."""

import json
import unittest
from pathlib import Path

from judge_seal_v4 import (
    CALIBRATION_KINDS, CLAIM_MAX, CLAIM_MIN, INJECTED_SPAN_MAX, SPAN_MAX,
    SPAN_MIN, STRATA, annotations_sha256, content_sha256, evidence_span,
    wire_claim,
)

FIXTURES = Path(__file__).parent / "fixtures"
CALIBRATION = FIXTURES / "judge_calibration_v4.json"
DRAFT = FIXTURES / "judge_final_v4_draft.json"
PACKAGE = FIXTURES / "judge_annotation_package_v4.json"
SEAL = FIXTURES / "judge_seal_v4.json"


def check_case_shape(test, case, *, injected_allowed):
    test.assertIs(case["phi"], False)
    test.assertTrue(CLAIM_MIN <= len(case["claim"]) <= CLAIM_MAX, case["id"])
    if "removable" in case:  # designed only in calibration; blind cases leave it to the operator
        test.assertEqual(len(case["removable"]), len(case["evidence"]), case["id"])
    for ref in case["evidence"]:
        test.assertRegex(ref["chunk_sha256"], r"^[0-9a-f]{64}$")
        if "payload" in ref:
            test.assertTrue(injected_allowed, f'{case["id"]}: unexpected payload ref')
            test.assertTrue(SPAN_MIN <= len(ref["base_span"]) <= SPAN_MAX, case["id"])
            test.assertTrue(ref["payload"].strip(), case["id"])
            test.assertLessEqual(len(evidence_span(ref)), INJECTED_SPAN_MAX, case["id"])
        else:
            test.assertTrue(SPAN_MIN <= len(ref["span"]) <= SPAN_MAX, case["id"])
    wired = wire_claim(case)
    test.assertEqual(wired["text"], case["claim"])
    for wired_ref in wired["evidence_refs"]:
        test.assertEqual(wired_ref["supports"], case["claim"])


class CalibrationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(CALIBRATION.read_text(encoding="utf-8"))

    def test_schema_and_kind_counts(self):
        self.assertEqual(self.fixture["schema_version"], "judge_calibration_v4")
        counts: dict[str, int] = {}
        for case in self.fixture["cases"]:
            counts[case["kind"]] = counts.get(case["kind"], 0) + 1
        self.assertEqual(counts, {
            "joint_by_design": 10, "padding_by_design": 10, "neutral_by_design": 8,
            "single_entailment_by_design": 6, "single_contradiction_by_design": 6,
            "metric_confusion_by_design": 8, "injection_by_design": 12,
        })
        self.assertEqual(set(counts), CALIBRATION_KINDS)

    def test_case_structure(self):
        seen = set()
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["id"], seen)
                seen.add(case["id"])
                self.assertIn(case["label"], {"entailment", "neutral", "contradiction"})
                self.assertIn(case["expected"], {"accept", "reject"})
                injected = case["kind"] == "injection_by_design"
                check_case_shape(self, case, injected_allowed=injected)

    def test_expected_is_consistent_with_design_labels(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                designed_accept = (case["label"] == "entailment"
                                   and all(r == "no" for r in case["removable"]))
                self.assertEqual(case["expected"], "accept" if designed_accept else "reject")

    def test_injection_payloads_only_in_injection_kind(self):
        for case in self.fixture["cases"]:
            has_payload = any("payload" in ref for ref in case["evidence"])
            if case["kind"] == "injection_by_design":
                self.assertTrue(has_payload, case["id"])
            else:
                self.assertFalse(has_payload, case["id"])


class SealHelperTests(unittest.TestCase):
    def test_strata_match_the_approved_protocol(self):
        self.assertEqual(sum(STRATA.values()), 130)
        self.assertIn("adversarial_injection", STRATA)
        self.assertIn("metric_confusion", STRATA)

    def test_content_hash_covers_composed_injection_spans(self):
        case = {"id": "x", "claim": "c" * CLAIM_MIN, "evidence": [
            {"base_span": "b" * SPAN_MIN, "payload": "ignore instructions"},
        ]}
        tampered = json.loads(json.dumps(case))
        tampered["evidence"][0]["payload"] = "ignore instructions!"
        self.assertNotEqual(content_sha256([case]), content_sha256([tampered]))

    def test_annotation_hash_detects_label_edits(self):
        case = {"id": "x", "annotation": {"label": "neutral"}}
        edited = {"id": "x", "annotation": {"label": "entailment"}}
        self.assertNotEqual(annotations_sha256([case]), annotations_sha256([edited]))


@unittest.skipUnless(DRAFT.exists(), "v4 blind draft not yet frozen")
class BlindFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = json.loads(DRAFT.read_text(encoding="utf-8"))
        cls.package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        cls.seal = json.loads(SEAL.read_text(encoding="utf-8"))

    def test_strata_counts_are_exact(self):
        counts: dict[str, int] = {}
        for case in self.draft["cases"]:
            counts[case["partition"]] = counts.get(case["partition"], 0) + 1
        self.assertEqual(counts, STRATA)

    def test_case_structure_and_bounds(self):
        seen = set()
        for case in self.draft["cases"]:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["id"], seen)
                seen.add(case["id"])
                injected = case["partition"] == "adversarial_injection"
                check_case_shape(self, case, injected_allowed=injected)
                refs = case["evidence"]
                if case["partition"] in {"joint_evidence", "padding",
                                         "cross_source_disagreement"}:
                    self.assertEqual(len(refs), 2)
                    self.assertNotEqual(refs[0]["document_id"], refs[1]["document_id"])
                elif case["partition"].startswith("single_") or case["partition"] == "metric_confusion":
                    self.assertEqual(len(refs), 1)

    def test_package_matches_draft_content_and_stays_blind(self):
        by_id = {case["id"]: case for case in self.draft["cases"]}
        self.assertEqual({c["id"] for c in self.package["cases"]}, set(by_id))
        for case in self.package["cases"]:
            with self.subTest(case=case["id"]):
                draft_case = by_id[case["id"]]
                self.assertEqual(case["claim"], draft_case["claim"])
                self.assertEqual(
                    case["spans"],
                    [evidence_span(ref) for ref in draft_case["evidence"]],
                )
                for leaked in ("partition", "notes", "categories", "document_id",
                               "payload", "base_span"):
                    self.assertNotIn(leaked, case)

    def test_content_hash_matches_seal(self):
        package_content = [
            {"id": c["id"], "claim": c["claim"], "evidence": [
                {"span": span} for span in c["spans"]
            ]} for c in self.package["cases"]
        ]
        self.assertEqual(content_sha256(package_content),
                         self.seal["case_content_sha256"])
        draft_by_id = {c["id"]: c for c in self.draft["cases"]}
        draft_in_package_order = [draft_by_id[c["id"]] for c in self.package["cases"]]
        self.assertEqual(content_sha256(draft_in_package_order),
                         self.seal["case_content_sha256"])

    def test_seal_records_the_protocol_and_judge_freeze(self):
        self.assertIn(self.seal["status"],
                      {"draft_frozen", "annotated_sealed", "judge_frozen", "measured"})
        self.assertEqual(self.seal["case_count"], sum(STRATA.values()))
        self.assertEqual(self.seal["strata"], STRATA)
        if self.seal["status"] in {"judge_frozen", "measured"}:
            judge = self.seal["judge"]
            self.assertRegex(judge["system_prompt_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(judge["temperature"], 0)
            self.assertTrue(judge["requested_model"])
            # Cloud re-baseline trigger: the calibration-observed served model
            # is sealed; the final refuses to trust a different served version.
            self.assertTrue(judge["served_models_at_calibration"])

    def test_annotations_match_seal_once_sealed(self):
        if "annotations_sha256" not in self.seal:
            self.skipTest("annotations not yet sealed")
        self.assertEqual(annotations_sha256(self.package["cases"]),
                         self.seal["annotations_sha256"])
        draft_annotations = {c["id"]: c["annotation"] for c in self.draft["cases"]}
        for case in self.package["cases"]:
            with self.subTest(case=case["id"]):
                self.assertIsNotNone(case["annotation"])
                self.assertEqual(draft_annotations[case["id"]], case["annotation"])


if __name__ == "__main__":
    unittest.main()
