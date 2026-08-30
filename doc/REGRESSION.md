# Public-corpus regression testing

## Purpose

The public validation datasets are a longitudinal regression suite for
`mailarchiver`. Running the same corpus at different repository revisions shows
whether acquisition, source recognition, parsing, routing, canonical MBOX
writing, metadata extraction, BagIt/Mailbag publication, or independent
verification changed over time.

This is broader than a pass/fail test. A run may produce a valid Mailbag while
changing message counts, duplicate handling, date resolution, parse defects,
or routing. Those differences must be visible and reviewed rather than hidden
by a final successful exit status.

The suite runs in two execution environments:

- locally, sequentially, beneath the ignored repository `data/` directory; or
- on AWS, with one independent ephemeral EC2 worker per dataset and results
  retained in a parameterized long-lived S3 bucket.

Both modes read the same dataset TOML and execute the same
`make validation-run DATASET=<id>` target. AWS changes scheduling, isolation,
and result retention; it does not provide a separate ingest implementation.
See [`validation/README.md`](../validation/README.md) for operating commands and
AWS parameters.

## Corpus policy

Only anonymously downloadable public sources belong in the suite. A dataset
configuration identifies its exact URLs, extraction limit, preprocessing mode,
expected message count when known, and EC2 size. Git sources use a pinned
commit. HTTP sources should gain an expected SHA-256 after their bytes have been
independently established; every acquisition records the observed SHA-256.

Fixed mailing-list samples deliberately cover different eras without making
every regression run download an entire high-volume list history. Changing the
sample months, source URL, expected digest, pinned Git commit, exclusions, or
preprocessing mode changes the test corpus. Such a change requires an explicit
review and a new baseline; it is not an ordinary implementation update.

Downloaded artifacts are evidence and remain unchanged. Babyl conversion and
removal of a Unix MBOX envelope from an individual-message corpus are derived
preprocessing operations. The source manifest identifies the retained inputs.

## Running a comparison

Start each comparison from a clean Mailbag result while retaining the download
cache. Run one dataset or the complete suite:

```console
make validation-run DATASET=spamassassin
make validation-run-all
```

For AWS, deploy the SAM control plane and launch one worker or all workers:

```console
make validation-aws-start DATASET=spamassassin STACK=mailarchiver-validation
make validation-aws-start-all STACK=mailarchiver-validation
```

## Metrics inventory

The regression report should eventually consolidate every metric below. The
status column describes the current implementation, not the importance of the
metric.

| Area | Desired comparison metric | Current availability |
| --- | --- | --- |
| Run identity | Repository commit, dirty-worktree flag, dataset configuration and configuration hash | Not in the JSON run report |
| Source identity | Source manifest hash, each HTTP artifact SHA-256, and each resolved Git commit | Manifest hash is in the JSON report; artifact identities are in the retained source manifest |
| Environment | Python, `mailarchiver`, ClamAV engine and signature versions; OS, architecture, local/AWS mode, and EC2 instance type | Not in the JSON run report |
| Acquisition | Downloaded bytes, extracted bytes, source-file count, and prepared-file count | Prepared-file count only is in the JSON report |
| Ingest disposition | Archived, duplicate, autosave-excluded, infected, failed, and unrecognized counts | Derivable from logs and the Mailbag; not consolidated |
| Canonical content | Canonical message count and sorted raw SHA-256 set | Derivable from canonical integrity tags; not consolidated |
| Routing | Counts by destination MBOX, year, and `Sent`/`Archive`/`INFECTED` category | Derivable from the Mailbag; not consolidated |
| Senders | Every normalized sender address, number of distinct messages sent, earliest message date, and latest message date | Derivable from the SQLite catalog; not consolidated |
| Recipients | Every normalized recipient address, number of distinct messages received, earliest message date, and latest message date, both combined and split by `To`, `Cc`, and `Bcc` | Derivable from the SQLite catalog where those headers are available; not consolidated |
| Message dates | Overall earliest/latest resolved message date plus counts with missing, invalid, or inferred dates | Derivable from the SQLite catalog and defect records; not consolidated |
| Metadata | Correspondent and attachment counts plus defect counts by field and defect type | Derivable from the SQLite catalog; not consolidated |
| AI results | AI processing status (`not_run`, `success`, `null`, or `error`); counts and percentages by status; model, provider, prompt/schema version; per-field non-null coverage; and a stable hash of each normalized result | Not implemented; the expected current baseline is `not_run` with all AI-result fields null |
| Verification | Independent verifier result, warnings, and failures | Printed by the verifier; not in the JSON run report |
| Performance | Wall-clock time for the complete run and for acquisition, extraction, preprocessing, ingest, verification, packaging, and S3 upload; peak resident memory and CPU time | Not measured in the JSON run report |
| Result location | Completion time, Mailbag path, ZIP path, ZIP SHA-256, AWS run ID, and S3 keys | Completion time and local paths/hash are in the JSON report; AWS status records contain AWS identifiers and keys |

Sender and recipient counts mean the number of distinct messages involving the
address, not the number of repeated address occurrences within a header. Address
normalization must be versioned and must retain a bucket for missing or
unparseable identities. Date ranges use the same resolved message date stored by
the catalog and report the number of messages excluded because no usable date
exists.

AI output has no correctness score until a dataset has independently labeled
ground truth. In the meantime, the useful regression signals are execution
status, non-null coverage, error rate, and normalized-result hashes. A model,
prompt, or schema change creates a new AI baseline and must not be compared as
though it were the same experiment.

Until these metrics are consolidated, preserve the source manifest, worker log,
canonical integrity tags, SQLite catalog, verifier output, and JSON run report
together. The JSON report alone is not a complete regression baseline.

## Comparison rules

The following are hard correctness gates for every run:

- acquisition matches every configured expected digest;
- preprocessing stays within the configured cumulative expansion limit and
  produces the configured expected count when present;
- ingest completes without silently dropping an encountered message;
- the installed `verify_mail_archive.py` reports success for BagIt, Mailbag,
  every canonical MBOX, and every declared raw and semantic message digest; and
- AWS workers upload their status and logs and terminate after success or
  failure.

With the same source manifest and dataset configuration, changes in any of the
following require investigation:

- prepared-file or canonical-message counts;
- raw message SHA-256 membership;
- duplicate, exclusion, infection, or routing counts;
- destination year/category distribution;
- resolved dates, correspondents, attachment counts, or metadata defects; or
- verifier warnings or failures.

Some values are expected to vary and must not be used alone as regression
identities:

- the final ZIP SHA-256, because ZIP member timestamps and generated BagIt
  metadata can change;
- SQLite database bytes or file ordering, because SQLite is rebuildable
  operational data;
- completion timestamps, paths, run IDs, and S3 keys; and
- elapsed time across different machines or ClamAV signature sets.

Compare canonical message hashes and structured counts, not just archive byte
identity. Performance comparisons require the same execution environment,
instance type, dataset hashes, ClamAV versions, and concurrency settings.

## Baselines and intentional changes

A baseline is an evidence bundle, not merely a ZIP checksum. It should contain
the run report, source manifest, dataset configuration, verifier output,
structured comparison metrics, and the repository revision. Large Mailbag ZIPs
belong in the configured S3 result location or another preservation store, not
in Git.

Do not automatically replace a baseline after a difference. First classify the
change as one of:

1. a defect or unintended regression;
2. an intentional parser, routing, metadata, or archive-format change;
3. a corpus change;
4. an environment or ClamAV-signature change; or
5. an unexplained difference requiring investigation.

An intentional baseline update records the old and new metrics, the responsible
commit or issue, and why the new behavior is correct. Preserve the preceding
baseline so behavior remains traceable across releases.

## Initial acceptance result

The first full local acceptance exercised the SpamAssassin Public Corpus. It
prepared and ingested 6,046 public messages, independently verified ten
canonical MBOX files, and produced a Mailbag ZIP. That run exposed two defects
which small fixtures had not revealed: preservation of a non-empty message
without a terminal newline and unfolding a folded `Message-ID` before writing
CRLF-only Mailbag CSV metadata. Both now have focused regression tests.

The SAM template has passed build and lint validation. A live AWS baseline has
not yet been established; it requires a published repository ref and explicit
region, existing bucket, VPC, and public-subnet parameters.
