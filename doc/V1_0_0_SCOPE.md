<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# v1.0.0 alpha scope review

Review date: 2026-09-07. This is release planning, not implementation or release
certification. No version tag or release binary has been published by this task.

Architecture update, 2026-09-10: [Dioxus Desktop and Tauri](DIOXUS.md) are candidates for
the compiled UI, retaining the Python archive engine. Windows full ingest is
the next platform priority; Linux/snap delivery is deferred. This supersedes
the earlier pywebview Windows direction, without claiming migration completion
or changing the historical release inventory below. Linked issue titles are
historical labels; this documentation update does not edit those issues.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

## Evidence and coverage

- GitHub inventory: all 50 open issues and three open PRs at the review snapshot.
  Every issue is classified below; open does not mean wholly unimplemented.
- Read available local project conversation history and recent app task records,
  including Application Interface and Installers, Gmail Import, Design archive
  redaction options, Authoritative names index, search/UI work, the repository
  audit, source-format planning, and website discussions. Historical local records
  include partial histories; the app lists only a bounded recent set. This is not
  a claim to have retrieved every ChatGPT conversation or every historical turn.
- Current README, requirements, implementation, distribution documentation,
  source registry, package version, and release workflow. Some broad historical
  implementation paragraphs are stale; use specific source/feature evidence.
- Reviewed local main: `1f8282862a8e6fbc2037c228c5f92dbcbf8f1a48`, seven commits
  ahead of the observed origin/main. Local delivery is not merged-release evidence.

## Proposed release boundary

The user's explicit scope is macOS, local and IMAP import, PST/OST import,
search/hiding/redaction, drag-to-Applications DMG installation, and GitHub update
notification/download. Apple Silicon only; early alphas may omit unfinished
milestone features. Windows is v1.1.0.

Confirmed deferrals: Contacts/geography, AI finding aids, OCR/PDF import, Gmail
API, Microsoft Graph, and primary-message-only search.

Include the supporting preservation contract, idempotent imports, recovery,
source provenance, GUI-only archive creation/import, multiple windows/archives,
message/attachment inspection and export, optional ClamAV, integrity verification,
accurate user documentation, and release-artifact validation.

Latest chat decisions add saved sources with Refresh/Rebuild and unsigned PDF
redaction reports. The current requirements still prescribe hashing on each
local reingest and non-destructive redacted derivatives; implementation of the
newly agreed workflows must update requirements and implementation together.

IMAP is still a reserved source stub. PST/OST and redaction remain planned.
No application updater was found in source. Local DMG packaging exists, but a
signed/notarized downloadable alpha and its upgrade path have not been validated
by this review. The package version remains `0.0.0`.

## Integration

All open PRs target main:

- #86 Complete desktop delivery and verified import lifecycle —
  `codex/desktop-delivery-publication`; includes #82's implementation.
- #83 Document mail acquisition and add Gmail authorizer —
  `codex/gmail-auth-helper`; overlaps delivery documentation/dependencies and
  includes authorization support, not a completed Gmail acquisition adapter.
- #82 Implement document controller and writer lease —
  `codex/issue-78-application-controller`; do not replay it independently of #86.

Reconcile these against the selected release commit and current-head checks.
No PR is merged or modified by this planning task.

## Decisions still needed

1. Confirm the first tag: suggested `v1.0.0-alpha.1`, pending confirmation and
   release-tool compatibility. Early alphas may omit unfinished features.
2. What minimum macOS version? Apple Silicon-only support is confirmed.
3. Does automatic download mean a staged DMG for manual installation or automatic
   replacement/relaunch? Define opt-out, check frequency, alpha/stable channel,
   architecture selection, artifact verification, and archive compatibility.
4. Decide whether encrypted malware quarantine (#62) and full Apple Mail
   reconstruction (#10) must ship in v1.0.0. Complete local EMLX import is
   narrower than #10. Other confirmed deferrals above are settled.
5. Set archive/schema compatibility expectations across alphas. Current schema
   validation rejects incompatible catalogs; automatic catalog migration is
   planned. An app updater cannot imply archive migration is already safe.

## Release validation, not yet performed here

Use the exact candidate commit for make check, packaging checks, the real native
GUI workflow, and mounted-DMG self-tests. Verify a downloaded artifact on a clean
supported Mac, Developer ID/notarization, bundled notices, architecture and minimum
OS metadata, and absence of developer-environment dependencies.

Add substantive Makefile validation for each newly implemented IMAP/PST/OST,
Refresh/Rebuild, redaction, and update behavior. Use a real disposable IMAP server;
check source immutability, item accounting, interruption recovery, and exact bytes
where the source supplies RFC 5322. Validate redaction integrity/recovery and both
PDFs. Test update selection/download/failure and installation without altering
archives. Do not count historical PR test reports as current-candidate validation.

## Open issue disposition

These are proposed assignments, not remote issue changes. Mixed-platform and
overlapping issues need scoped acceptance criteria; no issue is closed here.

| Issue | Proposed scope | Reason/status |
| --- | --- | --- |
| [#2 Add and validate ePADD interoperability for Mailbag archives](https://github.com/simsong/email-collection-toolkit/issues/2) | Proposed later / scope decision | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#4 Add read-only OST ingestion with explicit cache completeness](https://github.com/simsong/email-collection-toolkit/issues/4) | v1.0.0 core | OST cache may be incomplete; reconstructed MIME must be labeled. |
| [#5 Add read-only Gmail API acquisition with incremental history](https://github.com/simsong/email-collection-toolkit/issues/5) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#6 Add read-only PST ingestion with complete item accounting](https://github.com/simsong/email-collection-toolkit/issues/6) | v1.0.0 core | PST item accounting and reconstruction; not implemented. |
| [#7 Add read-only Microsoft 365 mailbox acquisition via Graph](https://github.com/simsong/email-collection-toolkit/issues/7) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#8 Add generic read-only IMAP acquisition with resumable UID checkpoints](https://github.com/simsong/email-collection-toolkit/issues/8) | v1.0.0 core | Overlaps #50; one IMAP implementation. |
| [#9 Add Thunderbird profile and offline IMAP-cache ingestion](https://github.com/simsong/email-collection-toolkit/issues/9) | Proposed later / scope decision | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#10 Complete Apple Mail package ingestion with detached-part accounting](https://github.com/simsong/email-collection-toolkit/issues/10) | Proposed later / scope decision | Decide full Apple Mail reconstruction vs existing complete EMLX import. |
| [#11 Add Eudora package ingestion with attachment reconstruction](https://github.com/simsong/email-collection-toolkit/issues/11) | Proposed later / scope decision | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#13 Add a source-folder-preserving canonical MBOX layout mode](https://github.com/simsong/email-collection-toolkit/issues/13) | Proposed later / scope decision | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#14 Create Tree of Original Mailboxes for Search Interface](https://github.com/simsong/email-collection-toolkit/issues/14) | v1.0.0 supporting; reconcile existing work | Existing source-tree implementation; reconcile issue status. |
| [#17 Create finding aid for mail archive using local AI agent (Apple Intelligence or PyTorch)](https://github.com/simsong/email-collection-toolkit/issues/17) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#18 Add OCR-backed ingest for standalone scanned email collections](https://github.com/simsong/email-collection-toolkit/issues/18) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#19 Index scanned PDF attachments with page-addressable OCR](https://github.com/simsong/email-collection-toolkit/issues/19) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#20 Build reproducible validation datasets from public email and Netnews archives](https://github.com/simsong/email-collection-toolkit/issues/20) | Validation support | Representative corpus evidence; full roadmap need not block early alpha. |
| [#21 Build public-corpus validation Mailbags locally and on ephemeral EC2 workers](https://github.com/simsong/email-collection-toolkit/issues/21) | Validation support | Validation engineering, not an end-user EC2 feature promise. |
| [#29 GUI: add in-window Command-F message find](https://github.com/simsong/email-collection-toolkit/issues/29) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#30 GUI: improve Mimestream-style address and subject suggestions](https://github.com/simsong/email-collection-toolkit/issues/30) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#31 Docs: document all Email Collection Toolkit GUI interactions](https://github.com/simsong/email-collection-toolkit/issues/31) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#32 GUI: preserve address-role choice after selecting a suggestion](https://github.com/simsong/email-collection-toolkit/issues/32) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#35 GUI: make Command-A context-sensitive in message headers](https://github.com/simsong/email-collection-toolkit/issues/35) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#36 GUI: anchor Locations to the bottom and expand the message pane](https://github.com/simsong/email-collection-toolkit/issues/36) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#37 GUI: hide Load more when no additional results exist](https://github.com/simsong/email-collection-toolkit/issues/37) | v1.0.0 supporting; reconcile existing work | Newer #66 removes manual pagination; do not restore obsolete UI. |
| [#38 GUI: make provenance offsets explicit and actionable](https://github.com/simsong/email-collection-toolkit/issues/38) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#39 Search: diagnose and accelerate unqualified text lookup](https://github.com/simsong/email-collection-toolkit/issues/39) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#40 GUI: show stable mail IDs and support direct mid-lookup](https://github.com/simsong/email-collection-toolkit/issues/40) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#41 GUI: distinguish message-file and attachment double-click behavior](https://github.com/simsong/email-collection-toolkit/issues/41) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#42 GUI: drag single messages as named mid-####.eml files](https://github.com/simsong/email-collection-toolkit/issues/42) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#43 GUI: select visible messages and export a ZIP](https://github.com/simsong/email-collection-toolkit/issues/43) | v1.0.0 supporting; reconcile existing work | Confirm acceptance against final release; open issue status alone does not establish implementation. |
| [#49 Decide the future of the UI](https://github.com/simsong/email-collection-toolkit/issues/49) | v1.0.0 supporting; reconcile existing work | Preserve #78 document model; trial Dioxus and Tauri before choosing the compiled UI; retain Python ingest. Migration remains outside the existing alpha implementation. |
| [#50 IMAP Source](https://github.com/simsong/email-collection-toolkit/issues/50) | v1.0.0 core | Reserved stub; add latest saved-source Refresh/Rebuild decisions. |
| [#51 Adopt rainbow-post icon and create the initial GitHub Pages site.](https://github.com/simsong/email-collection-toolkit/issues/51) | v1.0.0 supporting; reconcile existing work | Existing icon/site; verify alpha links and current feature claims. |
| [#62 Store ClamAV-positive messages as encrypted per-message EML files in a quarantine ZIP](https://github.com/simsong/email-collection-toolkit/issues/62) | Proposed later / scope decision | Decide encrypted quarantine ZIP vs current infected MBOX. |
| [#63 Make links in displayed email messages safe to inspect and open](https://github.com/simsong/email-collection-toolkit/issues/63) | v1.0.0 supporting; reconcile existing work | Recommend safe hover URL preview and click confirmation. |
| [#66 Virtualize large GUI search result lists](https://github.com/simsong/email-collection-toolkit/issues/66) | v1.0.0 supporting; reconcile existing work | Reconcile current virtualized UI and final selection/completeness tests. |
| [#69 Add a GUI-only archive creation and import workflow](https://github.com/simsong/email-collection-toolkit/issues/69) | v1.0.0 supporting; reconcile existing work | Local GUI import in #86; extend for promised sources. |
| [#70 Enforce one archive writer across GUI windows, CLI processes, and application instances](https://github.com/simsong/email-collection-toolkit/issues/70) | v1.0.0 supporting; reconcile existing work | Verify every new archive writer, including redaction. |
| [#71 Add pluggable antivirus providers for external ClamAV and Windows AMSI](https://github.com/simsong/email-collection-toolkit/issues/71) | v1.0.0 supporting; reconcile existing work | Mac ClamAV now; Windows AMSI/provider work later. |
| [#72 Add shared PyInstaller resource packaging and desktop release infrastructure](https://github.com/simsong/email-collection-toolkit/issues/72) | v1.0.0 supporting; reconcile existing work | Shared packaging used by Mac; Windows validation later. |
| [#73 Make the pywebview GUI fully functional on Windows](https://github.com/simsong/email-collection-toolkit/issues/73) | v1.1.0 Windows | Historical title; choose the compiled UI after Dioxus/Tauri trials and retain applicable full-ingest/native acceptance criteria. |
| [#74 Build, sign, notarize, and publish a self-contained macOS app and DMG](https://github.com/simsong/email-collection-toolkit/issues/74) | v1.0.0 core | Local DMG exists; public signed/notarized release unverified. |
| [#75 Build and Authenticode-sign a self-contained Windows installer](https://github.com/simsong/email-collection-toolkit/issues/75) | v1.1.0 Windows | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#76 Define and register the cross-platform Email Collection Toolkit document type](https://github.com/simsong/email-collection-toolkit/issues/76) | v1.0.0 supporting; reconcile existing work | Mac package registration now; Windows association later. |
| [#77 Define project copyright headers and package third-party license notices](https://github.com/simsong/email-collection-toolkit/issues/77) | v1.0.0 supporting; reconcile existing work | Inspect notices in exact locked release payload. |
| [#78 Adopt a document-centric multi-window archive architecture](https://github.com/simsong/email-collection-toolkit/issues/78) | v1.0.0 supporting; reconcile existing work | #86 includes #82; do not duplicate integration. |
| [#79 Allow reviewed manual geographic corrections for Contacts](https://github.com/simsong/email-collection-toolkit/issues/79) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#81 Define a visualization plug-in API](https://github.com/simsong/email-collection-toolkit/issues/81) | Proposed later / scope decision | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#85 Search: restrict matches to primary message text, excluding inline quoted messages](https://github.com/simsong/email-collection-toolkit/issues/85) | Deferred by user | Outside the initial alpha scope; see issue for detailed acceptance criteria. |
| [#88 V1: Hide messages and redact hidden MBOX records in place](https://github.com/simsong/email-collection-toolkit/issues/88) | v1.0.0 core | Planned; latest chat includes unsigned PDFs and JSONL reapplication. |
| [#89 V2: Signed redaction PDF reports and archive attestation identities](https://github.com/simsong/email-collection-toolkit/issues/89) | Deferred by user | Later signature phase is not necessarily product v2.0.0. |

## Tracking gaps

No dedicated automatic update-check/download issue was present. Reconcile latest
saved-source Refresh/Rebuild decisions into local/IMAP acceptance criteria.
