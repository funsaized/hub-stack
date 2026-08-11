"""Propositional span selection boundaries."""

import unittest

from app.spans import MAX_SPAN_CHARS, propositional_spans, sentence_bounds


# Real attempt-10 packed chunks. The first offered 13 spans and the second six;
# every one of them was reference debris, a fragment, or an unresolved
# demonstrative, and the claims drawn from them were all rejected.
BIBLIOGRAPHY_CHUNK = (
    'ing STARD-AI[15](https://www.nature.com/articles/s43856-024-00492-0#ref-CR15 '
    '"Sounderajah, V. et al. Developing a reporting guideline for artificial '
    'intelligence-centred diagnostic test accuracy studies: the STARD-AI protocol. '
    'BMJ Open. 11, e047709 \\(2021\\)."), TRIPOD-AI[16](https://www.nature.com/'
    'articles/s43856-024-00492-0#ref-CR16 "Collins, G. S. et al. Protocol for '
    'development of a reporting guideline \\(TRIPOD-AI\\) and risk of bias tool '
    '\\(PROBAST-AI\\) for diagnostic and prognostic prediction model studies based '
    'on artificial intelligence. BMJ Open. 11, e048008 \\(2021\\).")'
)

ANAPHORA_CHUNK = (
    't consideration (Supplementary Table [2](https://www.nature.com/articles/'
    's41591-020-1034-x#MOESM1)). This extension is aimed particularly at '
    'investigators and readers reporting or appraising clinical trials; however, it '
    'may also serve as useful guidance for developers of AI interventions in earlier '
    'validation stages of an AI system.'
)

CLEAN_CHUNK = (
    "The development of the CONSORT-AI guidance does not include additional items "
    "within the discussion section of trial reports. There is also recognition that "
    "AI is a rapidly evolving field, and there will be the need to update CONSORT-AI "
    "as the technology, and newer applications for it, develop."
)


class SpanSelectionTests(unittest.TestCase):
    def assertExact(self, text, spans):
        for span in spans:
            self.assertIn(span, text, "span is not an exact substring of its chunk")

    def test_every_span_is_an_exact_substring(self):
        for text in (BIBLIOGRAPHY_CHUNK, ANAPHORA_CHUNK, CLEAN_CHUNK):
            self.assertExact(text, propositional_spans(text))

    def test_reference_list_chunk_offers_nothing(self):
        self.assertEqual(propositional_spans(BIBLIOGRAPHY_CHUNK), [])

    def test_unresolved_demonstrative_chunk_offers_nothing(self):
        self.assertEqual(propositional_spans(ANAPHORA_CHUNK), [])

    def test_clean_chunk_keeps_both_statements(self):
        self.assertEqual(len(propositional_spans(CLEAN_CHUNK)), 2)

    def test_abbreviations_do_not_end_a_sentence(self):
        text = (
            "Collins, G. S. et al. reported that the guideline covers prediction "
            "model studies. A separate sentence follows the reported guideline claim."
        )
        self.assertEqual(len(sentence_bounds(text)), 2)

    def test_demonstrative_merges_with_its_predecessor(self):
        text = (
            "The CONSORT-AI extension was published for clinical trial reports. "
            "This extension is aimed particularly at investigators and readers."
        )
        spans = propositional_spans(text)
        self.assertEqual(len(spans), 2)
        self.assertTrue(spans[1].startswith("The CONSORT-AI extension was published"))
        self.assertTrue(spans[1].endswith("investigators and readers."))
        self.assertExact(text, spans)

    def test_chunk_initial_demonstrative_is_dropped(self):
        text = "This extension is aimed particularly at investigators and readers."
        self.assertEqual(propositional_spans(text), [])

    def test_existential_there_is_not_treated_as_anaphora(self):
        text = (
            "There is also recognition that AI is a rapidly evolving field requiring "
            "updates."
        )
        self.assertEqual(propositional_spans(text), [text])

    def test_fragments_headings_and_truncations_are_dropped(self):
        cases = {
            "lowercase continuation": "in live clinical settings the decisions affect care.",
            "heading": "### Box 1 Methodological challenges of the evaluation of systems",
            "no terminal punctuation": "The guidance was deemed translatable to trials for AI",
            "too few words": "Reporting guidelines are needed.",
            "page range": "The trial was reported in volume 25, 1467-1468 of the journal.",
            "year citation": "The guidelines were registered on the EQUATOR library (2019).",
            "table row": "| Item | Description | Guidance for reporting AI trials |",
            "too long": "The guidance " + "is restated " * 60 + "here.",
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                self.assertEqual(propositional_spans(text), [])

    def test_span_length_is_bounded_for_the_verifier_budget(self):
        for text in (BIBLIOGRAPHY_CHUNK, ANAPHORA_CHUNK, CLEAN_CHUNK):
            for span in propositional_spans(text):
                self.assertLessEqual(len(span), MAX_SPAN_CHARS)

    def test_duplicate_sentences_are_collapsed(self):
        body = "The reporting guideline covers the evaluation stage of the system."
        self.assertEqual(propositional_spans(f"{body} {body}"), [body])


if __name__ == "__main__":
    unittest.main()
