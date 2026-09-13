<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Related Work

This is a working literature map, not a claim that an existing method solves the
whole problem.

## Names and record linkage

- Christen's empirical comparison of personal-name matching techniques found
  material variation by data and method rather than a universal winner. It
  supports benchmarking multiple normalizers and similarities instead of
  choosing one by reputation: [Christen 2006, ANU repository](https://openresearch-repository.anu.edu.au/items/ffe40f0c-0751-4b23-aa2a-91355de9e0eb).
- Fellegi-Sunter remains the interpretable probabilistic foundation for many
  linkage systems. The modern entity-resolution review explains its comparison
  vectors, match/non-match weights, blocking, clustering, and evaluation:
  [Binette and Steorts 2022/2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11636688/).
- Blocking is part of model quality, not just an optimization. Murray's survey
  organizes blocking methods and their recall/computational tradeoffs:
  [Murray 2016](https://arxiv.org/abs/1603.07816).
- Adaptive and active-learning approaches can reduce required labels. Relevant
  early systems include [Bilenko and Mooney 2003](https://www.cs.utexas.edu/~ai-lab/pub-view.php?PubID=51499)
  and [Sarawagi and Bhamidipaty 2002](https://repository.ias.ac.in/128419/).
- Correspondent and thread relationships may improve ambiguous cases. Collective
  entity resolution explicitly models relational evidence:
  [Bhattacharya and Getoor 2007](https://linqs.org/assets/resources/bhattacharya-tkdd07.pdf).

Implication: begin with auditable comparison features and conservative blocking,
then test whether learned and collective methods improve held-out precision at a
fixed review budget.

## Email signatures and zones

- Carvalho and Cohen frame signature/reply extraction as line classification
  using structural and lexical features, without generative models:
  [CEAS 2004 paper](https://www.cs.cmu.edu/~vitor/publications/papers/carvalho04ceas.pdf).
- Lampert, Dale, and Paris classify email text into zones using graphical,
  orthographic, and lexical cues and report both fine- and coarse-grained
  experiments: [EMNLP 2009 paper](https://aclanthology.org/D09-1096.pdf).
- HPI publishes an annotated Enron-derived email-structure corpus with signature
  zones and relations including aliases and contact information:
  [HPI email structure corpus](https://hpi.de/naumann/projects/web-science/mimir-corpus-exploration-and-knowledge-management/email-structure.html).
- Mailgun's Forge repository contains public signature-extraction data and code
  useful for an independent baseline: [mailgun/forge](https://github.com/mailgun/forge).

Implication: signature extraction should be its own measured subsystem. Compare
delimiter/contact heuristics, repeated-tail learning, and supervised zone models.
Report both boundary quality and downstream identity-linkage effects.

## Email corpora and authorship

- The [CMU Enron corpus](https://www.cs.cmu.edu/~enron/) is public and large, but
  its folder organization is not a ready-made ground truth for all person/address
  equivalences. Klimt and Yang document the corpus and classification tasks:
  [CEAS 2004 / Springer record](https://doi.org/10.1007/978-3-540-30115-8_22).
- CEREC adds email coreference annotation and may help study person mentions, but
  its task differs from address identity linkage:
  [Dakle et al. 2021](https://arxiv.org/abs/2105.10606).
- The Avocado Research Email Collection is broader but licensed rather than
  freely redistributable: [LDC2015T03](https://catalog.ldc.upenn.edu/LDC2015T03).
- Email authorship work shows that writing style can carry sender evidence, but
  should be treated cautiously for short, quoted, or templated mail:
  [Estival et al. 2008](https://aclanthology.org/L08-1306/).
- S2AND is an author-name-disambiguation benchmark rather than email, but its
  multi-dataset evaluation and cross-dataset generalization problem are directly
  relevant: [Subramanian et al. 2021](https://arxiv.org/abs/2103.07534) and
  [dataset/code](https://github.com/allenai/S2AND).

Implication: Enron can test scale, signatures, list features, and selected
manually labeled identity cases. It should not be presented as comprehensive
identity ground truth. Independently held archives require consent, local
processing, a labeling protocol, and only aggregate publication.

## Evaluation and LLMs

- Entity resolution needs both pair and cluster evaluation. A recent framework
  discusses pairwise and B-cubed measures and entity-centric labeling:
  [Binette et al. 2024](https://arxiv.org/abs/2404.05622).
- LLM entity-resolution studies compare task formulations and selective use:
  [Peeters and Bizer 2024](https://arxiv.org/abs/2405.16884) and
  [Fan et al. 2024](https://arxiv.org/abs/2401.03426). They motivate testing an
  LLM only on bounded ambiguous candidates, not processing every message.
- OpenAI batch requests use per-request `custom_id` values for correlating
  asynchronous output: [OpenAI Batch API](https://developers.openai.com/api/reference/resources/batches).
- Gemini's batch API similarly supports JSONL keys and asynchronous job polling:
  [Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api).

Implication: an LLM experiment must report incremental precision/recall,
calibration, latency, token use, dollar cost, and disclosure exposure relative
to the same frozen candidate set. Local models are the default during development.

## Candidate implementations

- [Dedupe](https://github.com/dedupeio/dedupe) offers learned predicates,
  similarity weights, active labeling, and clustering.
- [Splink](https://github.com/moj-analytical-services/splink) implements scalable
  Fellegi-Sunter linkage, including a DuckDB backend.
- [Neo4j Community Edition](https://neo4j.com/open-source-project/) and
  [Apache AGE](https://age.apache.org/overview/) are plausible local graph
  experiments if collective inference warrants a server.
- [DuckPGQ](https://duckdb.org/docs/current/guides/sql_features/graph_queries) is
  attractive for embedded graph queries but currently documented as a community
  extension under active development, so it is not the baseline store.
