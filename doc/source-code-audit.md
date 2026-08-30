# Source-code audit

Date: 2026-08-25

## Scope

This audit covers all 30 tracked Python files in `src/mailarchiver/` and
`tests/`, the GUI JavaScript/HTML/CSS, the bundled Swift helper, and the
Makefile in the main checkout. It excludes `.tmp/`, virtual environments, and
generated archives. The review examined source immutability, MBOX recovery,
SQLite transactions and query plans, ClamAV lifecycle, resource cleanup,
MIME/export handling, asynchronous UI state, CLI validation, failure
atomicity, tests, and module-level documentation.

## Tightening completed

* Every tracked Python file now starts with a specific module docstring.
* Bounded search, year-report, provenance, integrity, and rebuild SQL use
  query-plan-tested catalog indexes. The complete current archive and search
  definitions are the packaged V1 schemas; obsolete development databases are
  deliberately rejected rather than migrated.
* FTS updates delete virtual rows by indexed metadata row IDs, and
  `refresh-index` validates MBOX/catalog identity and counts before replacing
  the prior disposable database.
* MBOX `From ` ambiguity recovery streams each candidate once instead of
  retaining every full candidate in a set; the installed stdlib verifier uses
  the same independent bounded order.
* A spawned ClamAV daemon must pass its configured health probe after socket
  creation. Owned processes receive bounded terminate/kill cleanup.
* Owner-token loading ignores indented comments, mutable Pydantic progress
  defaults use factories, duplicate pending-publication code is consolidated,
  and invalid worker counts, report limits, and descending year ranges fail
  during argument parsing.
* GUI attachment save/open checks no longer base64-encode payloads merely to
  inspect filename and MIME type, and child-window preview executors are
  closed. JavaScript request generations prevent stale search or MIME-part
  responses from overwriting newer state, and archive switching clears old
  drag exports and selections.
* SQLite schema constructors close their connections on setup failure.
* `--workers` now bounds simultaneously ingested source mailfiles. Each worker
  owns one file through planning, parsing, ClamAV scanning, and checkpoint,
  while duplicate admission and every canonical write remain serialized.
* Workers send typed status events to a main-thread driver. The terminal shows
  one numbered row per worker, active and peak concurrency, and width-bounded
  paths; worker threads never print or move the terminal cursor.

## Remaining implementation gaps

1. **Rollover and deterministic repacking.** `mailbox_name()` always selects
   part 1. The required 3.75 GiB rollover and run-completion
   `(resolved_date_utc, sha256)` repack are not implemented.
2. **Typed scanner outcomes.** `ClamScanner.infected()` returns only a boolean.
   Required `unscannable` and `scanner-error` outcomes, scanner/signature
   versions, and durable diagnostics need a typed result and routing policy.
   Consequently, most ingest acceptance tests invoke real ClamAV instead of
   reserving it for one integration test.
3. **Explicit MIME resource limits.** MIME parsing and GUI attachment decoding
   currently have no configured size, recursion, decompression, or time limits.
   Binary Tika extraction remains installer-only.
4. **Planned source adapters.** Gmail, live IMAP, Outlook PST/OST, Eudora, and
   offline IMAP cache adapters are design requirements, not current source.
5. **Native UI/helper coverage.** JavaScript-to-Python bridge smoke testing is
   manual/macOS-only beyond the existing service tests, and the Swift
   Foundation Models helper requires eligible Apple hardware for an end-to-end
   run.

The remaining items are architectural features rather than safe local cleanup;
they should receive focused designs and acceptance fixtures before changes to
canonical publication behavior.
