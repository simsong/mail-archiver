<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Reading double-processed MBOX records

When the first payload
line is exactly `>From ` followed by a sender and a ctime-style timestamp,
write it as a literal `X-From: sender timestamp` header, retaining the outer
delimiter. If the outer sender is exactly `XXX` or Eudora's `???@???` and the
inner sender is neither placeholder, use the inner envelope instead and write
the displaced outer value as `X-From:`. Other senders, including `foo@bar`,
`nobody` and `MAILER-DAEMON`, are not classified as bogus merely because they
look generic. Keep all remaining header and body bytes; never change sources.
The outer delimiter must itself be one complete literal From line with LF or
CRLF termination; embedded line breaks and malformed prefixes prevent
normalization. Only one immediate quoted line participates. Blank lines, indentation, ordinary
RFC headers, invalid delimiter syntax and additional `>` levels do not trigger
this rule. There is no scan into the body and no global removal of `>`.

For example:

```text
From XXX Thu Apr 15 04:21:10 2004
>From sender@example.test Thu Apr 15 00:20:49 2004
From: sender@example.test
Subject: Example

>From an intentional quotation
```

The normalized archive contains:

```text
From sender@example.test Thu Apr 15 00:20:49 2004
X-From: XXX Thu Apr 15 04:21:10 2004
From: sender@example.test
Subject: Example

>From an intentional quotation
```

Canonical SHA-256 covers the normalized RFC message, including `X-From:`.
Observation detail records typed normalization evidence: its rule, original
source-payload SHA-256, and exact original/quoted envelope bytes encoded as
base64. The original payload is reconstructable by replacing the inserted
first X-From line with the recorded quoted envelope. The original physical
envelope is separately retained in that evidence and, for a promotion, in the
literal header. This is an explicit exception to unchanged-message-byte import;
envelope values, line endings, remaining headers and body quoting are preserved.
This rule does not establish which program performed the double processing,
whether the mailbox was emailed, or which envelope timestamp is more accurate.

Exact empty MBCP metadata stubs remain excluded after normalization: the check
uses the selected envelope and original headers/body after the quoted delimiter.
Only the generated X-From is ignored; an existing X-From or nonempty body still
prevents exclusion. Failure history previews both original framing lines, even
when validation fails before an observation is stored.

## Compatibility evidence and limits

* **Procmail/formail:** the [upstream formail manual distributed by Debian](https://manpages.debian.org/trixie/procmail/formail.1.en.html)
  documents mailbox formatting and From-line escaping, a `foo@bar` fallback
  sender, and special handling of immediately following `>From` lines.
  It does not establish `XXX` as this file's producer signature. Fixtures cover
  standard and double-framed records without depending on a placeholder sender.
* **MIMEDefang/Sendmail:** the [MIMEDefang filter manual](https://www.mimedefang.org/man_mimedefang-filter.html)
  documents an operation that prepends a From-line for an MBOX-form scanner
  input. Such a line does not by itself justify removing any message content.
  Fixtures cover ordinary added framing and the selected double-framing pattern;
  `Received` and `X-Scanned-By` fields do not change parsing rules.
* **Eudora:** the [Eudora2Unix converter's format notes](https://eudora2unix.sourceforge.net/details.html)
  document `???@???` envelope senders and tested Eudora versions. Fixtures retain
  that sender and exercise LF/CRLF double framing. An `X-Mailer: Eudora` header
  identifies a composing client, not necessarily the tool that wrote a mailbox.

These are structural compatibility tests, not executions of those historical
products or proof of support for every variant. Detached Eudora attachments,
arbitrary Content-Length framing and restoration of ambiguous historical body
quoting are outside this rule. [RFC 4155](https://www.rfc-editor.org/rfc/rfc4155.html)
describes the variation among MBOX implementations and quoting conventions.

The older, explicit status-header `From XXX` wrapper remains a distinct format:
only a nonempty, valid status-only outer header block followed by a quoted delimiter at
its body boundary can be unwrapped. Malformed headers cannot cause that parser
to skip the real message headers and reinterpret an indented body line.

`make test-envelopes` checks unchanged body/header bytes, envelope precedence, literal
headers/MIME, source idempotence, independent archive verification, and failure
provenance. Existing archives are not rewritten; changed normalization may require a
separately authorized catalog rebuild or reconciliation of prior observations.
