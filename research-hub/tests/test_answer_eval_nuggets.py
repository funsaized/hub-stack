"""Structure of the HUB-047 answer-completeness set, checked without a corpus.

Span verification needs the document store and runs separately
(`tests/validate_answer_eval_nuggets.py`). Everything here runs in CI.
"""

import ast
import json
import unittest
from pathlib import Path

from tests.validate_answer_eval_nuggets import NUGGETS, QUESTIONS, normalise


VALIDATOR = Path(__file__).parent / "validate_answer_eval_nuggets.py"
SCOPE_KEYS = ("job_id", "topic_filter", "tags")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class QuestionSet(unittest.TestCase):
    def setUp(self):
        self.data = load(QUESTIONS)
        self.questions = self.data["questions"]

    def test_ids_are_unique(self):
        ids = [q["id"] for q in self.questions]

        self.assertEqual(len(ids), len(set(ids)))

    def test_every_question_names_a_bounded_scope(self):
        for question in self.questions:
            with self.subTest(question=question["id"]):
                present = [key for key in SCOPE_KEYS if question.get(key)]
                # An unfiltered corpus question would have no annotatable
                # scope: nuggets must come from the documents in scope, and
                # the whole corpus cannot be read exhaustively.
                self.assertEqual(len(present), 1, present)

    def test_job_questions_query_with_the_job_topic(self):
        for question in self.questions:
            if question["scope"] == "job":
                with self.subTest(question=question["id"]):
                    self.assertTrue(question.get("job_id"))

    def test_corpus_questions_carry_no_job_id(self):
        for question in self.questions:
            if question["scope"] == "corpus":
                with self.subTest(question=question["id"]):
                    self.assertNotIn("job_id", question)

    def test_both_scopes_are_represented(self):
        scopes = {question["scope"] for question in self.questions}

        self.assertEqual(scopes, {"job", "corpus"})

    def test_the_unfiltered_corpus_exclusion_is_recorded_not_silent(self):
        self.assertIn("unfiltered_corpus", self.data["excluded_scopes"])


class NuggetSet(unittest.TestCase):
    def setUp(self):
        self.data = load(NUGGETS)
        self.questions = {q["id"] for q in load(QUESTIONS)["questions"]}
        self.entries = self.data["questions"]

    def test_every_annotated_question_exists_in_the_question_set(self):
        for entry in self.entries:
            with self.subTest(question=entry["question_id"]):
                self.assertIn(entry["question_id"], self.questions)

    def test_pending_and_annotated_together_cover_the_question_set(self):
        annotated = {entry["question_id"] for entry in self.entries}
        pending = set(self.data["pending_questions"])

        self.assertEqual(annotated | pending, self.questions)
        self.assertEqual(annotated & pending, set())

    def test_nugget_ids_are_unique_within_a_question(self):
        for entry in self.entries:
            ids = [nugget["id"] for nugget in entry["nuggets"]]
            with self.subTest(question=entry["question_id"]):
                self.assertEqual(len(ids), len(set(ids)))

    def test_every_nugget_is_grounded_and_labelled(self):
        for entry in self.entries:
            for nugget in entry["nuggets"]:
                with self.subTest(nugget=f"{entry['question_id']}/{nugget['id']}"):
                    self.assertIn(nugget["importance"], ("vital", "okay"))
                    self.assertTrue(nugget["text"].strip())
                    self.assertTrue(nugget["source_document_id"].strip())
                    self.assertTrue(nugget["source_span"].strip())
                    self.assertLessEqual(len(nugget["source_span"]), 300)

    def test_vital_is_strict_enough_to_carry_information(self):
        for entry in self.entries:
            nuggets = entry["nuggets"]
            vital = sum(n["importance"] == "vital" for n in nuggets)
            with self.subTest(question=entry["question_id"]):
                # All-vital would make the label meaningless; none-vital would
                # leave the primary metric with an empty denominator.
                self.assertGreater(vital, 0)
                self.assertLess(vital / len(nuggets), 0.8)

    def test_no_two_nuggets_share_a_span(self):
        for entry in self.entries:
            spans = [normalise(n["source_span"]) for n in entry["nuggets"]]
            with self.subTest(question=entry["question_id"]):
                self.assertEqual(len(spans), len(set(spans)))

    def test_the_unverified_annotator_error_rate_is_disclosed(self):
        # The set is machine-annotated and not yet spot-checked. Whatever the
        # state, it has to be stated in the artefact rather than remembered.
        self.assertIn("spot_check", self.data["limitations"])
        self.assertIn("model_family", self.data["annotator"])


class ConstructionCannotSeeRetrieval(unittest.TestCase):
    def test_the_validator_does_not_import_the_retrieval_service(self):
        tree = ast.parse(VALIDATOR.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        # Nuggets mined from what retrieval surfaced could only ever reward
        # retrieval for surfacing them. The rule is enforced here rather than
        # left to reviewer memory.
        self.assertFalse([name for name in imported if name.startswith("app.")])


class Normalisation(unittest.TestCase):
    def test_folds_punctuation_that_survives_copying_badly(self):
        self.assertEqual(normalise("don’t — “x”"), normalise("don't - \"x\""))

    def test_collapses_whitespace_without_deleting_words(self):
        self.assertEqual(normalise("a \n  b"), "a b")

    def test_does_not_make_different_text_compare_equal(self):
        self.assertNotEqual(normalise("proxy_read_timeout"), normalise("proxy_send_timeout"))


if __name__ == "__main__":
    unittest.main()
