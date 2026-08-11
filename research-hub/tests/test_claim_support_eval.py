from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tests.build_claim_support_final_draft import build
from tests.claim_support_eval import select_threshold, validate_manifest, zero_failure_upper_bound
from tests.validate_claim_support_fixtures import audit_legacy


FIXTURES = Path(__file__).parent / "fixtures"


class ClaimSupportEvaluationTests(unittest.TestCase):
    def test_legacy_audit_and_critical_regression(self):
        summary = audit_legacy(FIXTURES / "claim_support_cases.json")
        self.assertEqual(summary["cases"], 28)
        self.assertTrue(summary["critical_regression_preserved"])

    def test_calibration_annotations_and_spans_validate(self):
        manifest = json.loads((FIXTURES / "claim_support_calibration_v2.json").read_text(encoding="utf-8"))
        summary = validate_manifest(manifest)
        self.assertEqual(summary["categories"], 38)
        self.assertFalse(summary["final_ready"])

    def test_unresolved_span_fails(self):
        manifest = json.loads((FIXTURES / "claim_support_calibration_v2.json").read_text(encoding="utf-8"))
        broken = copy.deepcopy(manifest)
        broken["cases"][0]["annotation"]["supporting_spans"][0]["text"] = "not supplied"
        with self.assertRaisesRegex(ValueError, "does not resolve exactly"):
            validate_manifest(broken)

    def test_final_draft_is_reproducible_and_not_evaluable(self):
        manifest = json.loads((FIXTURES / "claim_support_final_blind_draft_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, build())
        summary = validate_manifest(manifest)
        self.assertEqual(summary["draft_final_cases"], 405)
        with self.assertRaisesRegex(ValueError, "final gate requires"):
            validate_manifest(manifest, require_final_ready=True)

    def test_final_corpus_is_adjudicated_and_ready(self):
        manifest = json.loads((FIXTURES / "claim_support_final_v2.json").read_text(encoding="utf-8"))
        result = json.loads((FIXTURES / "claim_support_final_results_v2.json").read_text(encoding="utf-8"))
        summary = validate_manifest(manifest, require_final_ready=True)
        self.assertEqual(summary["adjudicated_final_supported"], 80)
        self.assertEqual(summary["adjudicated_final_unsupported"], 325)
        self.assertEqual(manifest["status"], "frozen")
        self.assertEqual(result["metrics"]["totals"]["accepted_unsupported"], 0)
        self.assertEqual(result["policy"]["threshold"], 0.97)

    def test_threshold_objectives_and_sample_size(self):
        rows = [
            {"threshold": .90, "false_support_rate": 0.0, "supported_claim_retention": .8},
            {"threshold": .95, "false_support_rate": 0.0, "supported_claim_retention": .8},
            {"threshold": .99, "false_support_rate": 0.0, "supported_claim_retention": .7},
        ]
        self.assertEqual(select_threshold(rows)["threshold"], .95)
        self.assertLess(zero_failure_upper_bound(299), .01)
        self.assertGreater(zero_failure_upper_bound(298), .01)


if __name__ == "__main__":
    unittest.main()
