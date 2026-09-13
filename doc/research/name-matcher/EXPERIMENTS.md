<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Experimental Protocol

## Research questions

1. Which evidence sources improve address-to-person linkage while maintaining a
   very high precision target?
2. Can non-LLM signature extraction supply reliable alias/contact evidence at
   million-message scale?
3. Do temporal and correspondent-graph features identify shared accounts,
   account handoffs, and ambiguous names better than record-local features?
4. At a fixed review budget of 100–200 pairs per session, which active selection
   policy produces the largest quality gain?
5. Does bounded LLM adjudication improve a conventional matcher enough to
   justify its latency, cost, and privacy exposure?
6. How well do models developed on one private archive transfer to public and
   independently held archives?

## Frozen stages

Each experiment records dataset version/fixity, extraction configuration and
version, candidate-generator version, feature version, matcher and parameters,
random seed, training labels, threshold policy, and decision-log snapshot.
Candidate sets are frozen before engines are compared. No engine receives extra
manual labels unless that label budget is part of the experiment.

### Data splits

- **Development:** Simson's archive, with identity owners and time strata split
  so near-duplicate messages cannot cross train/test boundaries.
- **Public external:** CMU Enron for scale and manually adjudicated identity
  subsets; HPI/Forge for signature boundaries and contact facts; S2AND for an
  out-of-domain name-disambiguation stress test.
- **Independent private:** Marian's, Marvin's, or other archives only after
  consent and a local, non-disclosing protocol. Keep one archive untouched by
  feature development when possible.

Separate entity-level train/development/test splits are mandatory. A random
message split leaks repeated signatures, addresses, threads, and quoted content.

## Ground truth and labeling

Label candidate pairs `match`, `non-match`, or `insufficient evidence`. Record
the evidence available to the reviewer and whether the answer is known from the
archive owner. Double-label a stratified subset and report agreement. Sample:

- high-score automatic merges;
- pairs in the review band;
- below-threshold candidates;
- pairs missed by each blocking strategy; and
- addresses classified as list, role, shared, automated, or temporal handoff.

The gold set must include difficult negatives: relatives, same-name people,
shared organizations, role accounts, forwarded signatures, and recycled
addresses. Gold labels and development labels are versioned and never inferred
from matcher output.

## Metrics

The primary safety metric is pairwise precision for accepted links, with a
predeclared lower confidence bound. Also report:

- pairwise recall and F-score;
- B-cubed precision, recall, and F-score for clusters;
- over-merge and under-merge counts, with largest error-component size;
- calibration (reliability plot, Brier score, expected calibration error);
- blocking recall and candidate-pairs-per-record;
- precision and recall by evidence source, decade, address type, and name class;
- review yield, decisions per minute, and quality gain per 150 decisions;
- signature line/boundary F-score and typed-fact precision/recall;
- list/shared/handoff classification precision and recall;
- wall time, CPU time, peak memory, database size, and messages/second; and
- for LLMs, input/output tokens, latency, failed jobs, estimated and actual cost,
  and incremental quality per dollar.

Because false merges are more harmful, threshold selection maximizes recall
subject to a high held-out precision constraint; it does not maximize aggregate
F-score. Report performance across the full threshold curve so the chosen policy
is auditable.

## Matcher matrix

| Engine | Local | Training | Relational evidence | Probability | Initial role |
|---|---:|---:|---:|---:|---|
| Precision rules | yes | no | limited | calibrated separately | safety baseline |
| Fellegi-Sunter/logistic | yes | optional | engineered | yes | interpretable statistical baseline |
| Dedupe | yes | active labels | limited | score/calibration study | learned linkage candidate |
| Splink/DuckDB | yes | optional EM/labels | engineered | yes | scalable probabilistic candidate |
| Collective graph model | yes | labels/priors | native | model-dependent | correspondence/thread experiment |
| Local LLM reranker | yes | prompting | summarized | must calibrate | uncertainty-band experiment |
| OpenAI/Gemini batch | no | prompting | summarized | must calibrate | separately authorized cost study |

## Ablations

For each conventional engine, compare cumulative feature sets:

1. header names and email local parts;
2. plus temporal ranges and domain/organization features;
3. plus heuristic signatures;
4. plus repeated-tail or supervised signature evidence;
5. plus reply/thread and correspondent-neighborhood evidence;
6. plus account-kind and handoff constraints; and
7. optional LLM adjudication of the same uncertainty band.

Signature experiments must measure downstream linkage, not only extraction.
Graph experiments must beat the relational baseline on held-out quality or
review efficiency, not merely demonstrate graph queries.

## Results tables to preserve

### Linkage quality

| Dataset/split | Engine | Features | Auto threshold | Pair P | Pair R | B3 P | B3 R | Over-merges | Review yield |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|

### Signature extraction

| Dataset | Extractor | Boundary P/R/F1 | Email P/R | Phone P/R | Name P/R | Msg/s |
|---|---|---:|---:|---:|---:|---:|

### Account classification

| Dataset | Method | Person F1 | List F1 | Shared F1 | Handoff F1 | Unknown rate |
|---|---|---:|---:|---:|---:|---:|

### LLM increment and cost

| Provider/model | Candidate band | N | Pair P/R delta | Tokens | Cost | Median latency | Failures |
|---|---|---:|---:|---:|---:|---:|---:|

Blank cells stay blank until a reproducible run supplies them. Development
results and final held-out results must be visibly separated.
