"""Regenerate the deterministic three-message Mailbag fixture used by tests."""

from __future__ import annotations

import argparse
import hashlib
import mailbox
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mailarchiver.bagit import write_bag_checkpoint
from mailarchiver.catalog import address_pk, create_catalog
from mailarchiver.layout import mbox_directory


MESSAGES = (
    (
        "one@example.test",
        b"Message-ID: <one@example.test>\nFrom: Alice <alice@example.test>\n"
        b"To: archive@example.test\nDelivered-To: archive@example.test\n"
        b"Subject: First fixture message\nDate: Mon, 01 Jan 2024 10:00:00 +0000\n\n"
        b"The first preserved body.\n",
    ),
    (
        "two@example.test",
        b"Message-ID: <two@example.test>\nFrom: Bob <bob@example.test>\n"
        b"To: archive@example.test\nSubject: Mutable status fixture\n"
        b"Date: Tue, 02 Jan 2024 11:00:00 +0000\nStatus: RO\n\n"
        b"Status is excluded only from the semantic digest.\n",
    ),
    (
        "three@example.test",
        b"Message-ID: <three@example.test>\nFrom: Carol <carol@example.test>\n"
        b"To: archive@example.test\nSubject: Attachment fixture\n"
        b"Date: Wed, 03 Jan 2024 12:00:00 +0000\n"
        b"MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=fixture\n\n"
        b"--fixture\nContent-Type: text/plain; charset=utf-8\n\nThe third preserved body.\n"
        b"--fixture\nContent-Type: text/plain; name=note.txt\n"
        b"Content-Disposition: attachment; filename=note.txt\n\nAttached text.\n"
        b"--fixture--\n",
    ),
)


def generate(output: Path) -> None:
    """Replace only the designated fixture directory with deterministic content."""
    if output.exists():
        shutil.rmtree(output)
    mbox_directory(output).mkdir(parents=True)
    (output / "integrity").mkdir()
    path = mbox_directory(output) / "three-messages.mbox"
    with path.open("wb") as destination:
        for ordinal, (_, raw) in enumerate(MESSAGES, 1):
            destination.write(f"From fixture{ordinal}@example.test Mon Jan  1 00:00:0{ordinal} 2024\n".encode())
            destination.write(raw)

    catalog = create_catalog(output / "archive.sqlite3")
    try:
        box = mailbox.mbox(path, factory=None, create=False)
        try:
            keys = list(box.iterkeys())
            if len(keys) != len(MESSAGES):
                raise ValueError("fixture MBOX did not retain exactly three messages")
            generation_pk = catalog.execute(
                "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 0, 0)",
                (path.name,),
            ).lastrowid
            for key, (message_id, expected_raw) in zip(keys, MESSAGES, strict=True):
                raw = box.get_bytes(key, from_=False)
                if raw != expected_raw:
                    raise ValueError(f"fixture MBOX changed {message_id}")
                sender_pk = address_pk(catalog, f"fixture-{message_id}")
                message_pk = catalog.execute(
                    "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, "
                    "date_utc, date_source, category) VALUES (?, ?, ?, '', '2024-01-01T00:00:00+00:00', "
                    "'date', 'Archive')",
                    (message_id, hashlib.sha256(raw).hexdigest(), sender_pk),
                ).lastrowid
                start, stop = box._lookup(key)
                catalog.execute(
                    "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
                    (message_pk, generation_pk, start, stop - start),
                )
        finally:
            box.close()
        catalog.commit()
        (output / "bag-info.txt").write_text(
            "External-Identifier: mailarchiver-three-message-test-fixture\n", encoding="utf-8"
        )
        write_bag_checkpoint(output, catalog, datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc))
        catalog.commit()
    finally:
        catalog.close()
    (output / "archive.sqlite3").unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    generate(parser.parse_args().output)


if __name__ == "__main__":
    main()
