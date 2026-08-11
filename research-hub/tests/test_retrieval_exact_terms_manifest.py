"""Deterministic validation of the exact-term retrieval probe manifest."""

import copy
import json
import unittest
from pathlib import Path

from tests.benchmark_retrieval_exact_terms import CATEGORIES, DEFAULT_MANIFEST, load


class ExactTermManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load(DEFAULT_MANIFEST)

    def test_manifest_loads_and_validates(self):
        self.assertEqual(self.manifest["schema_version"], "1.0.0")
        self.assertGreaterEqual(len(self.manifest["cases"]), 10)

    def test_every_query_quotes_an_exact_term(self):
        for case in self.manifest["cases"]:
            self.assertTrue(
                any(term.lower() in case["query"].lower() for term in case["exact_terms"]),
                case["id"],
            )

    def test_categories_are_known_and_all_probed(self):
        seen = {case["category"] for case in self.manifest["cases"]}
        self.assertEqual(seen, CATEGORIES)

    def test_case_ids_are_unique(self):
        ids = [case["id"] for case in self.manifest["cases"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_rejects_paraphrased_query(self):
        broken = copy.deepcopy(self.manifest)
        broken["cases"][0]["query"] = "What does the checklist require about errors?"
        path = Path(self.enterContext(_temp_manifest(broken)))
        with self.assertRaisesRegex(ValueError, "must quote"):
            load(path)

    def test_rejects_unknown_category(self):
        broken = copy.deepcopy(self.manifest)
        broken["cases"][0]["category"] = "misc"
        path = Path(self.enterContext(_temp_manifest(broken)))
        with self.assertRaisesRegex(ValueError, "unknown category"):
            load(path)

    def test_rejects_duplicate_ids(self):
        broken = copy.deepcopy(self.manifest)
        broken["cases"].append(copy.deepcopy(broken["cases"][0]))
        path = Path(self.enterContext(_temp_manifest(broken)))
        with self.assertRaisesRegex(ValueError, "duplicate case id"):
            load(path)


class _temp_manifest:
    def __init__(self, manifest: dict):
        self.manifest = manifest

    def __enter__(self) -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(self.manifest, handle)
        handle.close()
        self.path = handle.name
        return self.path

    def __exit__(self, *_exc) -> None:
        import os

        os.unlink(self.path)


if __name__ == "__main__":
    unittest.main()
