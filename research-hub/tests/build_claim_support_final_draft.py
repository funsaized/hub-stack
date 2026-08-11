"""Materialize the deterministic, unlabeled final-review draft (not a test set)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random


POLICY = "The cited evidence set, read together and without outside knowledge, entails the entire claim."


def unsupported(kind: str, i: int) -> tuple[list[str], str]:
    token = f"{kind.replace('_', ' ').title()} study {i + 1}"
    n = 100 + i
    cases = {
        "contradiction": ([f"{token} reduced errors by {10 + i} percent."], f"{token} increased errors by {10 + i} percent."),
        "neutral_related": ([f"{token} describes Framework Alder and Framework Birch."], "Framework Alder was derived from Framework Birch."),
        "missing_qualifier": ([f"{token} reported accuracy of 0.{70 + i}."], f"{token} reported external-validation accuracy of 0.{70 + i}."),
        "negation": ([f"{token} did not reduce mortality."], f"{token} reduced mortality."),
        "wrong_entity": ([f"Model Alder {i + 1} reached sensitivity 0.91."], f"Model Birch {i + 1} reached sensitivity 0.91."),
        "wrong_population": ([f"{token} reduced symptoms in adults."], f"{token} reduced symptoms in children."),
        "wrong_intervention": ([f"Treatment Alder {i + 1} reduced pain."], f"Treatment Birch {i + 1} reduced pain."),
        "wrong_comparator": ([f"Model Alder {i + 1} outperformed Model Birch."], f"Model Alder {i + 1} outperformed usual care."),
        "wrong_outcome": ([f"{token} reduced length of stay."], f"{token} reduced mortality."),
        "wrong_number": ([f"{token} enrolled {n} adults."], f"{token} enrolled {n + 50} adults."),
        "wrong_unit": ([f"{token} measured 5 mg/L."], f"{token} measured 5 mg/dL."),
        "wrong_ci": ([f"{token} estimated 1.4 (95% CI 1.1 to 1.8)."], f"{token} estimated 1.4 (95% CI 0.8 to 1.8)."),
        "significance": ([f"{token} measured 0.88 for Alder and 0.86 for Birch."], "Alder was significantly more accurate than Birch."),
        "causal": ([f"{token} found tool use was associated with fewer errors in an observational cohort."], "The tool caused fewer errors."),
        "temporal": ([f"{token} found accuracy at baseline."], f"{token} found accuracy persisted for two years."),
        "study_stage": ([f"{token} began an exploratory feasibility study."], f"{token} proved efficacy in a completed phase III trial."),
        "modality": ([f"{token} says the tool may reduce processing time."], "The tool must reduce processing time."),
        "quantifier": ([f"Three of four sites in {token} improved."], f"All four sites in {token} improved."),
        "broader_scope": ([f"{token} validated the model at one hospital."], "The model is validated for every hospital."),
        "bibliography": ([f"River A. {token}: a reporting guideline. Public Methods Journal."], f"{token} improves trial quality."),
        "fragmented": ([f"In {token}, ...was associated with a 12 percent improvement."], f"The AI intervention in {token} improved diagnostic accuracy by 12 percent."),
        "partial_compound": ([f"{token} found Framework Alder covers early evaluation."], "Framework Alder covers early evaluation and mandates a fixed design."),
        "false_multisource": ([f"{token}: Framework Alder covers protocols.", "Framework Birch covers prediction reports."], "Framework Alder requires Framework Birch."),
        "conflict": ([f"The registry for {token} lists {n} participants.", f"The report for {token} lists {n - 1} participants."], f"The final enrollment for {token} was {n} participants."),
        "lexical_overlap": ([f"{token} described treatment, mortality, and a significance plan; no mortality result was reported."], f"{token} found treatment significantly reduced mortality."),
    }
    return cases[kind]


def supported(kind: str, i: int) -> tuple[list[str], str]:
    token = f"Supported study {kind.replace('_', ' ')} {i + 1}"
    cases = {
        "exact": ([f"{token} reported sensitivity 0.91."], f"{token} reported sensitivity 0.91."),
        "paraphrase": ([f"Review errors fell by {10 + i} percent under {token}."], f"{token} reduced review errors by {10 + i}%."),
        "negation": ([f"{token} found no increase in serious adverse events."], f"{token} did not increase serious adverse events."),
        "narrower": ([f"All four sites in {token}, including Cedar, improved."], "Site Cedar improved."),
        "compound": ([f"{token} reported sensitivity 0.91 and specificity 0.84."], f"{token} had 0.91 sensitivity and 0.84 specificity."),
        "multisource": ([f"{token}: cohort A improved.", f"{token}: cohort B improved."], f"Cohorts A and B improved in {token}."),
        "unit": ([f"{token} measured 5 milligrams per liter (mg/L)."], f"{token} measured 5 mg/L."),
        "ci": ([f"{token} estimated 1.4 (95% confidence interval 1.1 to 1.8)."], f"{token} estimated 1.4 with a 95% CI of 1.1 to 1.8."),
        "existential": ([f"Three of four sites in {token} improved."], f"At least one site in {token} improved."),
        "padding": (["Administrative meetings occurred monthly.", f"{token} reported sensitivity 0.91.", "The appendix used blue headings."], f"{token} reported sensitivity 0.91."),
    }
    return cases[kind]


def build() -> dict:
    rows: list[tuple[list[str], str]] = []
    for kind in ("contradiction", "neutral_related", "missing_qualifier", "negation", "wrong_entity", "wrong_population", "wrong_intervention", "wrong_comparator", "wrong_outcome", "wrong_number", "wrong_unit", "wrong_ci", "significance", "causal", "temporal", "study_stage", "modality", "quantifier", "broader_scope", "bibliography", "fragmented", "partial_compound", "false_multisource", "conflict", "lexical_overlap"):
        rows.extend(unsupported(kind, i) for i in range(13))
    for kind in ("exact", "paraphrase", "negation", "narrower", "compound", "multisource", "unit", "ci", "existential", "padding"):
        rows.extend(supported(kind, i) for i in range(8))
    random.Random(20260811).shuffle(rows)
    return {
        "schema_version": "2.0.0",
        "corpus_id": "claim-support-final-blind-draft-2026-08-11",
        "status": "draft",
        "policy": {"support": POLICY, "accept_label": "entailment", "outside_knowledge": False},
        "coverage": {"required_categories": []},
        "cases": [{
            "id": f"final-draft-{index:04d}", "partition": "final_blind", "status": "draft",
            "claim": claim, "evidence": evidence, "categories": [], "critical": False, "phi": False,
        } for index, (evidence, claim) in enumerate(rows, 1)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
