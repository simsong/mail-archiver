<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit v1.0.0 alpha — draft release notes

Status: unreleased scope draft, 2026-09-07. The first alpha tag and minimum macOS version are pending.
Apple Silicon only; early alphas may omit unfinished v1.0.0 milestone features.
These notes describe the proposed v1.0.0 destination, not a tested release artifact.
Remove or explicitly defer unfinished features before publishing release notes.

Email Collection Toolkit brings local and remote email together into a searchable personal
archive on your Apple Silicon Mac. Windows support is planned for v1.1.0.

## Installation and updates

- Install a self-contained Mac application by opening its DMG and dragging
  Email Collection Toolkit into Applications. No Python or developer tools are required
  for the supported desktop workflow.
- Planned: check GitHub for new releases, indicate an available version, and
  automatically download the appropriate update. Whether installation remains
  manual is undecided. The updater is not implemented in the reviewed source.
- Developer ID signing, notarization, downloaded-DMG installation, and the
  supported macOS/architecture matrix must be verified for the actual release.
  Current local packaging supports ad-hoc signing and builds for its native
  architecture; that is not evidence of notarized public distribution.

## Bring your email together

- Import local MBOX, Emacs RMAIL Babyl, EML, Maildir messages, and complete
  Apple Mail EMLX files. Google Takeout MBOX uses the local import path.
  Complete cached messages can be imported; this does not establish that an
  Apple Mail cache contains the whole remote mailbox or every attachment.
- Planned: read-only IMAP import over TLS, configurable folders and concurrent
  downloads, credentials in the OS keychain, and resumable UID checkpoints.
  Generic IMAP support does not itself establish Gmail/Microsoft 365 login
  compatibility; provider authentication must be tested and documented.
- Planned: Outlook PST and OST import with complete item accounting. Report
  incomplete cache contents and reconstructed MIME explicitly. OST import is
  limited to content present locally; Outlook for Mac OLM is outside this scope.
- Planned from the latest source-management discussion: save local file,
  local folder, and IMAP sources in per-archive configuration. Import/Refresh
  revisits configured sources and uses the agreed modification-time shortcut
  for local sources; Import/Rebuild rehashes all source files. This shortcut
  intentionally misses changes that preserve modification times and is not
  the current content-hashing implementation.
- Monitor import progress and history, cancel safely, and rerun an interrupted
  import without duplicating previously archived identical messages.

## Search and inspect

- Search the complete archive using message text, address, subject, and date
  filters, with suggestions and stable message identifiers.
- Browse original source folders and inspect where each message was found.
- Read text, HTML, legacy x-html, and raw source; highlight search matches
  and find text within the displayed message.
- Open multiple search windows and archives, inspect attachments, and export
  messages as EML or a selected-message ZIP.
- Retain responsive large-result browsing and verify ordering, counts, selection,
  and completeness against the final release. Older pagination tickets must be
  reconciled with the newer virtualized-results design.

## Hide and redact — planned

- Hide/unhide messages reversibly. Ordinary searches omit hidden messages;
  `hidden:` finds them. Show Hidden, Unhide All, and a configurable pink
  appearance make the state visible.
- Explicitly redact hidden messages in the archive by overwriting their MBOX
  record ranges while preserving unaffected records. Source mail is untouched.
- Include a message-count confirmation with a ten-second countdown, redaction
  history with original/replacement hashes, updated integrity metadata, and
  removal of original message content from managed SQL/search data.
- Produce two unsigned PDF reports: one with message metadata and hashes, and
  one with hashes only. Record a JSONL redaction log with an option to reapply it.
- An index rebuild warns before discarding hidden flags and leaves redacted
  MBOX content in place. Source reimport can restore original messages; automatic
  reimport suppression is not promised. Recovery and short-record handling still
  require implementation decisions and validation in issue #88.
- Digital signatures, signing identities, and encrypted reversible sealing are
  outside the initial hide/redaction implementation. Redaction does not promise
  removal from source mail, filesystem snapshots, or backups.

## Preservation and antivirus

- Archives use BagIt/Mailbag as their native storage format, with MBOX payloads,
  SHA-256 integrity information, source provenance, and an independent verifier.
  SQLite catalogs and search indexes are derived operational data.
- Preserve acquired RFC 5322 bytes, including malformed mail. Deduplication
  requires matching raw content identity and normalized Message-ID; distinct
  raw messages are not collapsed merely because semantic hashes match.
- Use an optional separately installed/configured ClamAV scanner. Clearly warn
  when it is unavailable and require an explicit choice to import unscanned.
  Scanner failures must not silently become clean results.
- Retain positively detected mail in the current infected-MBOX representation.
  Encrypted quarantine ZIP storage remains a separate scope decision (#62).

## Deferred from the proposed v1.0.0 scope

Windows GUI/installer and Windows antivirus integration target v1.1.0. The
compiled UI candidates are [Dioxus Desktop and Tauri](DIOXUS.md), retaining Python ingest
and preservation. This migration is planned and is not part of the current
pywebview alpha implementation. Windows delivery requires full ingest; Linux/snap
delivery is deferred. Proposed
later work includes Gmail API and Microsoft Graph acquisition, full legacy-client
package reconstruction, OCR and scanned-email PDF import, AI finding aids,
Contacts/People/geography research interfaces, visualization plugins, ePADD
interoperability, alternative canonical layouts, and primary-message-only search.
The user confirmed deferral of Contacts/geography, AI finding aids, OCR/PDF
import, Gmail API, Microsoft Graph, and primary-message-only search. The other
deferrals remain recommendations.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

See [the scope and issue review](V1_0_0_SCOPE.md) for open-issue dispositions,
integration status, unresolved release decisions, and validation requirements.
