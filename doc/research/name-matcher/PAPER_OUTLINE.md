<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Paper Outline

Working title: **Precision-First Temporal Identity Resolution in Longitudinal
Email Archives**

1. **Introduction** — authoritative name search, aliases, false-merge risk,
   shared accounts, review limits, and contributions.
2. **Problem formulation** — observations versus assertions; many-address person
   clusters; non-unique names; temporal/shared account membership.
3. **Related work** — record linkage, name matching, blocking, collective entity
   resolution, email signatures/zones, authorship, and LLM entity resolution.
4. **Corpora and ethics** — canonical-byte preservation, private local analysis,
   consent, development on Simson's archive, public corpora, and held-out design.
5. **System** — verified extraction, signature subsystem, temporal evidence
   graph, account classification, blocking, scoring, constraints, and review UI.
6. **Experimental design** — engines, ablations, label budgets, thresholds,
   transfer, metrics, compute, and cost.
7. **Results** — linkage, signatures, account types/handoffs, review efficiency,
   generalization, runtime, and LLM incremental value.
8. **Error analysis** — false merges, missed links, common names, families,
   organizations, forwarded signatures, mailing lists, and address recycling.
9. **Limitations** — incomplete ground truth, language/culture coverage, archive
   selection, temporal gaps, privacy, and dependence on header integrity.
10. **Conclusion and reproducibility** — code, public configurations, synthetic
    fixtures, aggregate results, and the boundary around non-redistributable data.

The paper must not claim that private development data is independent test data.
Every table should identify dataset, split, extractor, candidate generator,
matcher version, threshold, and label budget.
