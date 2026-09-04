"""Generate the deterministic, virus-free source corpus committed for E2E tests."""

from __future__ import annotations

import argparse
from pathlib import Path


RICH_MESSAGE = (
    b"Message-ID: <rich-e2e@example>\n"
    b"Date: Tue, 31 Dec 2024 12:00:00 +0000\n"
    b"Received: by old-outlier.example; Wed, 31 Jan 2024 12:00:00 +0000\n"
    b"Received: by first.example; Thu, 1 Feb 2024 12:00:00 +0000\n"
    b"Received: by second.example; Fri, 2 Feb 2024 12:00:00 +0000\n"
    b"Received: by new-outlier.example; Sun, 4 Feb 2024 12:00:00 +0000\n"
    b'From: "Curator" <curator@example.net>\nTo: archive-owner@example.org\nCc: Beth Rosenberg <beth@example.org>\n'
    b"Subject: Rich UI message\nMIME-Version: 1.0\n"
    b"Content-Type: multipart/mixed; boundary=outer\n\n"
    b"--outer\nContent-Type: multipart/alternative; boundary=alternative\n\n"
    b"--alternative\nContent-Type: text/plain; charset=utf-8\n\n"
    b"Plain E2E body for the message viewer.\n"
    b"--alternative\nContent-Type: text/html; charset=utf-8\n\n"
    b'<html><head><style>mark { background: yellow !important; }</style></head><body>'
    b'<mark class="message-find-match message-find-current" data-mailarchiver-find-target="outer">decoy</mark>'
    b'<span id="message-find-0">decoy</span><p>HTML E2E body for the message viewer.</p>'
    + b"<br>" * 180
    + b'<p>Curator one.</p><p>Curator two.</p>'
    b'<img src="https://tracker.invalid/pixel.png"></body></html>\n'
    b"--alternative\nContent-Type: text/html; charset=utf-8\n\n"
    b'<html><body><p>Secondary HTML alternative.</p><img src="https://tracker.invalid/secondary.png"></body></html>\n'
    b"--alternative--\n"
    b"--outer\nContent-Type: image/png\nContent-Disposition: attachment; filename=tiny.png\n"
    b"Content-Transfer-Encoding: base64\n\n"
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=\n"
    b"--outer\nContent-Type: text/plain; charset=utf-8\n"
    b"Content-Disposition: attachment; filename=review.command\n\n"
    b"Appendixquartz occurs only in this risky attachment.\n"
    b"--outer--\n"
)


def basic_message(number: int) -> bytes:
    day = number % 28 + 1
    return (
        f"Message-ID: <bulk-{number:03d}-e2e@example>\n"
        f"Date: Mon, {day:02d} Jan 2024 10:{number % 60:02d}:00 +0000\n"
        "From: sender@example.net\nTo: archive-owner@example.org\n"
        f"Subject: Bulk message {number:03d}\n\n"
        f"Body preview for deterministic bulk message {number:03d}.\n"
    ).encode("ascii")


def mbox_bytes(messages: list[bytes]) -> bytes:
    chunks = []
    for message in messages:
        chunks.append(b"From fixture@example.net Mon Jan  1 00:00:00 2024\n" + message.rstrip(b"\n") + b"\n\n")
    return b"".join(chunks).rstrip(b"\n") + b"\n"


def generate(destination: Path) -> None:
    (destination / "Professional/Inbox").mkdir(parents=True, exist_ok=True)
    (destination / "Professional/Projects").mkdir(parents=True, exist_ok=True)
    (destination / "Professional/Drafts").mkdir(parents=True, exist_ok=True)
    (destination / "Personal/Loose Mail").mkdir(parents=True, exist_ok=True)
    (destination / "Personal/Duplicates").mkdir(parents=True, exist_ok=True)
    bulk = [RICH_MESSAGE, *(basic_message(number) for number in range(1, 204))]
    (destination / "Professional/Inbox/bulk.mbox").write_bytes(mbox_bytes(bulk))
    edge = [
        b"Message-ID: <mboxrd-e2e@example>\nDate: Sat, 3 Feb 2024 12:00:00 +0000\n"
        b"From: sender@example.net\nTo: archive-owner@example.org\nSubject: MBOX quoting\n\n"
        b">From unquoted body line\n>>From literal quoted body line\n",
    ]
    (destination / "Professional/Projects/edge-cases.mbox").write_bytes(mbox_bytes(edge))
    (destination / "Professional/Projects/no-final-newline.eml").write_bytes(
        b"Message-ID: <no-final-newline-e2e@example>\nDate: Fri, 2 Feb 2024 12:00:00 +0000\n"
        b"From: sender@example.net\nTo: archive-owner@example.org\nSubject: No final newline\n\n"
        b"Source semantics omit a final newline."
    )
    (destination / "Personal/Loose Mail/001-single.eml").write_bytes(
        b"Message-ID: <single-e2e@example>\nDate: Sun, 4 Feb 2024 12:00:00 +0000\n"
        b"From: Beth Rosenberg <beth@example.org>\nTo: archive-owner@example.org\n"
        b"Subject: Your Flight Receipt - ELISABETH COUSIN\n\n"
        b"A folder containing only single-message files becomes one logical mailbox.\n"
    )
    (destination / "Personal/Duplicates/rich-duplicate.eml").write_bytes(RICH_MESSAGE)
    (destination / "Professional/Drafts/autosave.eml").write_bytes(
        b"Message-ID: <autosave-e2e@example>\nDate: Mon, 5 Feb 2024 12:00:00 +0000\n"
        b"From: archive-owner@example.org\nTo: recipient@example.net\nSubject: Excluded autosave\n"
        b"X-Apple-Auto-Saved: 1\n\nThis draft is intentionally excluded.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    generate(parser.parse_args().destination)


if __name__ == "__main__":
    main()
