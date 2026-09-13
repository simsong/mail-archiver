<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Authoritative Name Index Research

This directory is the research notebook and reproducible prototype for resolving
email addresses, observed human names, aliases, and temporal account ownership
into reviewable person identities. The work has two equal deliverables:

1. maintainable, source-preserving software for Email Collection Toolkit; and
2. an academic-quality study of identity resolution in longitudinal email.

No matcher has been selected. Rule-based linkage, probabilistic record linkage,
learned linkage, collective graph methods, and optional LLM adjudication are
experiments to compare. The system is precision-first: a false merge is more
damaging than a missed merge, and every automatic merge must be explainable and
reversible.

Development lives on the long-running `research-name-matching` branch. The
branch contains research documentation, extraction and evaluation code, fixtures,
and reproducible result formats; it does not contain production UX work. Keep it
current by merging `main` into it, preserving an explicit merge history rather
than rebasing the shared research record.

## Development data and validity

The current private archive has 1,200,791 catalogued messages, 219,074 email
addresses, 102,322 distinct sending addresses, and 154,915 distinct recipient
addresses. Its canonical MBOX payload is about 39.7 GB across 81 generations.
The disposable search database contains 214,926 address suggestions, but about
43 percent have no display name and it retains only one preferred display name
per address. The prototype therefore returns to verified canonical message bytes
and retains every observation.

Simson Garfinkel's archive is development data. It must not be the only reported
evaluation corpus, and repeated inspection makes it unsuitable as an untouched
final test set. The paper must clearly disclose this development use. Public or
independently held corpora must supply final evaluation evidence; proposed
corpora and their limitations are in [EXPERIMENTS.md](EXPERIMENTS.md).

Private archive content must remain local. A cloud-provider experiment requires
an explicit, separately authorized run and a documented disclosure policy.

## First executable slice

The current prototype performs read-only evidence extraction:

```shell
make name-matcher-observations \
  ARCHIVE=/Users/simsong/mail-archive \
  OUTPUT=.tmp/name-evidence.sqlite3 \
  ARGS='--limit 10000 --workers 4'
```

It writes a private SQLite database plus a YAML summary and refuses to overwrite
an existing output. It extracts all display-name/address observations from
`From:`, `To:`, `Cc:`, and `Bcc:`. It separately extracts conservative signature
candidates and their names, email addresses, phone numbers, URLs, and retained
text lines. It does not use Subject text or ordinary body text as name-search
evidence.

The output filesystem must support hard links and owner-only file permissions.
The builder publishes a completed database with an exclusive hard link from
its same-filesystem temporary directory, so a destination created during
extraction is not overwritten. If hard-link publication is unsupported, the
build fails; there is no rename fallback. A plain
[Python `os.rename`](https://docs.python.org/3/library/os.html#os.rename)
can overwrite an existing file on Unix, even within the same filesystem.

The first signature detector is deliberately non-LLM. It uses standard signature
delimiters, mobile footers, bottom-of-message contact anchors, short signoffs,
and quoted-reply boundaries. Each result is evidence with a method and
confidence, not a claim that its contents identify the sender. Repeated-tail
learning by sender is planned because consistency across messages can provide
stronger evidence than a single heuristic result.

## Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) defines the temporal evidence graph,
  pipeline, search semantics, review model, and parallel execution.
- [EXPERIMENTS.md](EXPERIMENTS.md) defines datasets, splits, metrics, ablations,
  and result tables.
- [LITERATURE.md](LITERATURE.md) records related work and how it bears on this
  problem.
- [PAPER_OUTLINE.md](PAPER_OUTLINE.md) keeps the software and paper questions
  aligned.
- [RUN_LOG.md](RUN_LOG.md) records local smoke experiments without private
  message content.
- [prototype_schema.sql](prototype_schema.sql) is the disposable research schema.

Run focused validation with `make test-name-resolution`. All generated databases,
signature text, review files, and provider request files are private derivatives
and belong under ignored `.tmp/` storage.
