# Claim-support annotation guide

Judge only whether the supplied evidence, read together, makes the complete claim true. Do
not use outside medical, scientific, or world knowledge. Quantities, units, population,
entity, intervention, comparator, outcome, polarity, modality, time, stage, causality, and
scope are material.

- **Entailment:** every material claim component follows from the evidence. Ordinary wording
  changes and explicit arithmetic are allowed.
- **Contradiction:** the evidence establishes that at least one material component is false.
- **Neutral:** the evidence is related but leaves at least one material component unknown.
- Unresolved conflicting evidence is neutral; do not use logical explosion to call it support.
- Only entailment is accepted by the gate. If uncertain between entailment and another label,
  choose the non-entailment label and explain the missing or refuted component.

For every review, record the label, one-sentence rationale, and each material component. Copy
exact supporting, refuting, or merely relevant substrings from the supplied evidence; do not
paraphrase spans. Empty supporting/refuting lists are correct when no such span exists.

Final-blind procedure:

1. Freeze and shuffle case text before labels are exposed to model selection.
2. Two reviewers label every case independently without seeing the other label, intended
   stratum, model outputs, or calibration results.
3. An adjudicator resolves every disagreement and spot-checks agreements. Reviewer identities,
   both labels, the final label, rationale, components, and exact spans remain in the fixture.
4. Run the deterministic validator; repair malformed annotations, never model errors.
5. Seal a corpus hash and threshold before the one-time final evaluation. Any content change
   creates a new version and invalidates the prior result.

All examples must be synthetic, public, or de-identified and explicitly set `phi` to `false`.
Do not include names, dates of birth, record numbers, addresses, or other identifying details.
