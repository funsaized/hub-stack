"""Phase 4 deterministic retrieval and citation evaluation gate."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "report_retrieval_cases.json"


def run_benchmark(manifest: Path = MANIFEST) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tests.benchmark_report_retrieval", "--manifest", str(manifest)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class ReportRetrievalBenchmarkTests(unittest.TestCase):
    def test_command_is_deterministic_and_reports_required_metrics(self):
        first = run_benchmark()
        second = run_benchmark()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["metrics"], {
            "citation_validity": 1.0,
            "critical_recall_at_k": 1.0,
            "duplicate_rate": 0.0,
            "precision_at_k": 0.5,
            "recall_at_k": 1.0,
            "reciprocal_rank": 1.0,
            "source_coverage": 1.0,
            "unsupported_claim_rejection_count": 4,
        })
        self.assertEqual(report["gates"], {
            "citation_validity": True,
            "critical_recall_at_k": True,
            "passed": True,
        })

    def test_coverage_is_reported_beside_recall_and_gates_nothing(self):
        report = json.loads(run_benchmark().stdout)

        coverage = report["coverage"]
        self.assertEqual(len(coverage["cases"]), len(report["cases"]))
        # Breadth never becomes a pass/fail: the gate keys are exactly the two
        # relevance gates (HUB-044). Coverage@k is maximised by one chunk per
        # source, so gating on it would reward shredding arguments.
        self.assertEqual(
            set(report["gates"]), {"citation_validity", "critical_recall_at_k", "passed"}
        )
        for case in coverage["cases"]:
            selected = case["selected"]
            self.assertGreaterEqual(
                selected["sources_in_scope"], selected["sources_represented"]
            )
            for point in selected["curve"]:
                self.assertLessEqual(
                    point["sources_at_k"], point["attainable_sources_at_k"]
                )
                self.assertLessEqual(point["sources_at_k"], point["chunks_at_k"])
        for name in ("selected", "uncapped"):
            self.assertEqual(
                [point["k"] for point in coverage[name]],
                sorted(point["k"] for point in coverage[name]),
            )
            self.assertTrue(all(point["cases"] == len(report["cases"])
                                for point in coverage[name]))

    def test_lifting_the_per_source_cap_cannot_lower_breadth(self):
        report = json.loads(run_benchmark().stdout)

        # The comparison that says whether the cap or the ranking produced the
        # breadth. On the synthetic manifest the cap never binds, so the two
        # curves coincide — which is exactly why the live probe exists.
        for capped, uncapped in zip(
            report["coverage"]["selected"], report["coverage"]["uncapped"]
        ):
            self.assertEqual(capped["k"], uncapped["k"])
            self.assertGreaterEqual(
                uncapped["chunks_at_k"], capped["chunks_at_k"]
            )

    def test_command_fails_each_critical_gate(self):
        original = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mutations = {
            "critical_recall_at_k": lambda data: data["cases"][0][
                "expected_sentinels"
            ].append({"document_id": "doc-late", "text": "MISSING_SENTINEL"}),
            "citation_validity": lambda data: data["cases"][0]["valid_claims"].append(
                "Invalid expected citation [S99]."
            ),
        }
        for gate, mutate in mutations.items():
            with self.subTest(gate=gate), tempfile.TemporaryDirectory() as directory:
                manifest = json.loads(json.dumps(original))
                mutate(manifest)
                path = Path(directory) / "manifest.json"
                path.write_text(json.dumps(manifest), encoding="utf-8")

                result = run_benchmark(path)

                self.assertEqual(result.returncode, 1, result.stderr)
                report = json.loads(result.stdout)
                self.assertFalse(report["gates"][gate])
                self.assertFalse(report["gates"]["passed"])


if __name__ == "__main__":
    unittest.main()
