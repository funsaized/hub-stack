"""The HUB-044 coverage-baseline manifest, validated without live services."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.benchmark_retrieval_coverage import DEFAULT_MANIFEST, load


def written(manifest: dict) -> Path:
    directory = tempfile.mkdtemp()
    path = Path(directory) / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class ShippedManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = load(DEFAULT_MANIFEST)

    def test_covers_both_scopes(self):
        self.assertGreaterEqual(len(self.manifest["job_cases"]), 1)
        self.assertGreaterEqual(len(self.manifest["corpus_cases"]), 1)

    def test_corpus_cases_include_a_filtered_and_an_unfiltered_scope(self):
        cases = self.manifest["corpus_cases"]
        filtered = [
            case for case in cases
            if case.get("topic_filter") is not None or case.get("tags")
        ]
        unfiltered = [case for case in cases if case not in filtered]

        # The unfiltered corpus is the scope with no honest denominator; the
        # filtered one is bounded. Measuring only one would hide that (HUB-044).
        self.assertTrue(filtered)
        self.assertTrue(unfiltered)

    def test_every_job_case_queries_with_the_job_topic(self):
        for case in self.manifest["job_cases"]:
            self.assertEqual(case["query"], case["topic"], case["id"])

    def test_case_ids_are_unique_across_both_scopes(self):
        ids = [
            case["id"]
            for case in [*self.manifest["job_cases"], *self.manifest["corpus_cases"]]
        ]

        self.assertEqual(len(ids), len(set(ids)))

    def test_records_where_its_cases_came_from(self):
        self.assertIn("HUB-044", self.manifest["provenance"])


class Validation(unittest.TestCase):
    def setUp(self):
        self.original = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    def mutated(self, mutate) -> Path:
        manifest = json.loads(json.dumps(self.original))
        mutate(manifest)
        return written(manifest)

    def test_rejects_a_job_case_whose_query_drifted_from_its_topic(self):
        path = self.mutated(
            lambda data: data["job_cases"][0].update({"query": "something else"})
        )

        with self.assertRaises(ValueError):
            load(path)

    def test_rejects_a_corpus_case_carrying_a_job_id(self):
        path = self.mutated(
            lambda data: data["corpus_cases"][0].update({"job_id": "abc"})
        )

        with self.assertRaises(ValueError):
            load(path)

    def test_rejects_duplicate_case_ids(self):
        path = self.mutated(
            lambda data: data["corpus_cases"][0].update(
                {"id": data["job_cases"][0]["id"]}
            )
        )

        with self.assertRaises(ValueError):
            load(path)

    def test_rejects_an_empty_scope(self):
        for name in ("job_cases", "corpus_cases"):
            with self.subTest(name=name):
                path = self.mutated(lambda data, name=name: data.__setitem__(name, []))

                with self.assertRaises(ValueError):
                    load(path)

    def test_rejects_malformed_ks_tags_and_versions(self):
        mutations = [
            lambda data: data.__setitem__("schema_version", "2.0.0"),
            lambda data: data.__setitem__("provenance", ""),
            lambda data: data.__setitem__("extra_ks", [0]),
            lambda data: data.__setitem__("extra_ks", ["4"]),
            lambda data: data["corpus_cases"][0].update({"tags": "llm"}),
            lambda data: data["corpus_cases"][0].update({"topic_filter": 7}),
            lambda data: data["job_cases"][0].update({"job_id": ""}),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                with self.assertRaises(ValueError):
                    load(self.mutated(mutate))


if __name__ == "__main__":
    unittest.main()
