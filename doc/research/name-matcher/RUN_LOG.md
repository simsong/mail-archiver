<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Experimental Run Log

This tracked log contains aggregate reproducibility evidence only. Private
databases, message text, signature text, addresses, names, and request payloads
remain in ignored local storage.

## 2026-09-03 — extraction smoke test

- Branch: `research-name-matching`
- Base after final synchronization: `origin/main` at `499ae6f`
- Input: Simson development archive, read-only
- Selection: first 2,000 catalogued messages allocated in filename order across
  13 MBOX generations; this is a path/integrity smoke test, not a random or
  representative evaluation sample
- Workers: 4 process workers, independent SQLite shards
- Extractor: `mailarchiver-name-observations` 0.1.0, heuristic signature v1
- Command shape: `make name-matcher-observations ARCHIVE=... OUTPUT=... ARGS='--limit 2000 --workers 4'`

| Measure | Value |
|---|---:|
| Messages | 2,000 |
| Extraction errors | 0 |
| Header observations | 4,840 |
| Transport/list markers | 261 |
| Distinct addresses | 1,331 |
| Distinct nonempty normalized names | 464 |
| Signature candidates | 428 |
| Signature facts | 2,712 |

Signature candidate methods were 333 contact blocks, 78 delimiter blocks, and
17 signoffs. Facts comprised 360 email occurrences, 280 phone occurrences, 124
name candidates, one URL occurrence, and 1,947 retained text lines. These are
unadjudicated detector outputs, not precision or recall results.

The combined database and YAML summary were both mode `0600`; SQLite foreign-key
validation returned no violations. Each canonical message was accepted only
after SHA-256 verification through the existing MBOX location reader.

Post-sync focused validation: `make test-name-resolution` reported 6 passed;
`make pylint` rated the tree 10.00/10; `git diff --check` reported no errors.
Before the final main fast-forward, the full unit suite reported 234 passed and
one skipped (the optional local 86 MB encoding fixture was absent). This full
suite result is recorded as pre-sync and is not substituted for post-sync
focused validation.
