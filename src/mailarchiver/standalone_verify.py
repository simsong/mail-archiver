#!/usr/bin/env python3
"""Independently generate and verify Mailbag and message fixity using only stdlib."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import mailbox
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable

BUFFER_SIZE = 1024 * 1024
FORMAT_ID = "tag:simson.net,2026:mailarchiver/integrity"
FORMAT_VERSION = 1
INTEGRITY_SUFFIX = ".integrity"
INSTALLED_NAME = "verify_mail_archive.py"
TABLE_HEADER = "ordinal\tmessage-id-json\thashes..."
BAGIT_DECLARATION = b"BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
PAYLOAD_MANIFEST = "manifest-sha256.txt"
TAG_MANIFEST = "tagmanifest-sha256.txt"
MAILBAG_HEADERS = (
    "Error", "Mailbag-Message-ID", "Message-ID", "Original-File",
    "Message-Path", "Derivatives-Path", "Attachments",
)
CODE_PATTERN = re.compile(r"h[1-9][0-9]*")
HEX_PATTERN = re.compile(r"[0-9a-f]+")
SHA256_PATTERN = re.compile(r"[0-9A-Fa-f]{64}")
MAILBAG_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
SPLIT_CSV_PATTERN = re.compile(r"mailbag-([1-9][0-9]*)\.csv")
MAILBAG_ROW_LIMIT = 100_000
MAX_AMBIGUOUS_FROM_LINES = 12
FIELD_NAME_PATTERN = re.compile(rb"[!-9;-~]+")
ALGORITHMS = {"sha256": 64, "sha512": 128}

TYPE = "type"
CODE = "code"
DIGEST_ALGORITHM = "digest_algorithm"
FORMAT_ID_KEY = "format_id"
FORMAT_VERSION_KEY = "format_version"
HASH_STANDARD = "hash_standard"
HASH_VERSION = "hash_version"
ID = "id"
INPUT = "input"
SCOPE = "scope"
CANONICALIZATION = "canonicalization"
SAME_INPUT_AS = "same_input_as"
MANIFEST_ID = "manifest_id"
BYTES = "bytes"
HASHES = "hashes"
MESSAGES = "messages"
NAME = "name"
COLUMNS = "columns"
ENCODING = "encoding"

SEMANTIC_DOMAIN = b"tag:simson.net,2026:mailarchiver/hash/semantic/v1\0"
SEMANTIC_HEADERS = (
    b"from", b"sender", b"reply-to", b"to", b"cc", b"bcc", b"delivered-to",
    b"date", b"message-id", b"subject", b"mime-version", b"content-type",
    b"content-transfer-encoding", b"content-disposition",
)
SEMANTIC_CANONICALIZATION = {
    "body": {"length": "entire", "method": "dkim-simple"},
    "domain_separator": "tag:simson.net,2026:mailarchiver/hash/semantic/v1\0",
    "headers": {
        "method": "dkim-relaxed",
        "occurrences": "all",
        "order": [item.decode("ascii") for item in SEMANTIC_HEADERS],
        "repeated_field_order": "bottom-to-top",
    },
    "line_endings": "crlf-from-crlf-lf-or-cr",
}


@dataclass(frozen=True)
class IntegrityMessage:
    """One message row supplied by the canonical archive catalog."""

    message_id: str | None
    raw_sha256: str
    raw: bytes


@dataclass(frozen=True)
class HashStandard:
    """A validated hash-standard declaration."""

    code: str
    algorithm: str
    standard: str
    version: int
    scope: str
    same_input_as: str | None

    def digest(self, data: bytes) -> str:
        return hashlib.new(self.algorithm, data).hexdigest()


def _json_bytes(value: object) -> bytes:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"{encoded}\n".encode("utf-8")


def _normalize_line_endings(raw: bytes) -> bytes:
    return re.sub(rb"\r\n|\r|\n", b"\r\n", raw)


def semantic_bytes(raw: bytes) -> bytes:
    """Return semantic-v1 input bytes using the documented DKIM profile."""
    normalized = _normalize_line_endings(raw)
    header_block, separator, body = normalized.partition(b"\r\n\r\n")
    if not separator:
        body = b""
    fields: list[tuple[bytes, bytes]] = []
    current_name: bytes | None = None
    current_value = b""
    for line in header_block.split(b"\r\n"):
        if line.startswith((b" ", b"\t")) and current_name is not None:
            current_value += b"\r\n" + line
            continue
        if current_name is not None:
            fields.append((current_name, current_value))
            current_name = None
        name, marker, value = line.partition(b":")
        if marker and FIELD_NAME_PATTERN.fullmatch(name):
            current_name, current_value = name.lower(), value
    if current_name is not None:
        fields.append((current_name, current_value))

    output = bytearray(SEMANTIC_DOMAIN)
    for selected in SEMANTIC_HEADERS:
        for name, value in reversed(fields):
            if name == selected:
                unfolded = re.sub(rb"\r\n[ \t]+", b" ", value)
                relaxed = re.sub(rb"[ \t]+", b" ", unfolded).strip(b" \t")
                output.extend(name + b":" + relaxed + b"\r\n")
    output.extend(b"\r\n")
    output.extend(body.rstrip(b"\r\n") + b"\r\n")
    return bytes(output)


def _digest_file(path: Path, algorithms: Iterable[str]) -> dict[str, str]:
    digests = {algorithm: hashlib.new(algorithm) for algorithm in algorithms}
    with path.open("rb") as source:
        for block in iter(lambda: source.read(BUFFER_SIZE), b""):
            for digest in digests.values():
                digest.update(block)
    return {algorithm: digest.hexdigest() for algorithm, digest in digests.items()}


def _standard_records() -> list[dict[str, object]]:
    return [
        {
            CANONICALIZATION: {"method": "none"}, CODE: "h1", DIGEST_ALGORITHM: "sha256",
            HASH_STANDARD: "mbox", HASH_VERSION: 1,
            ID: "tag:simson.net,2026:mailarchiver/hash/mbox/v1/sha256",
            INPUT: "complete-mbox-file", SCOPE: "mbox", TYPE: "hash-standard",
        },
        {
            CANONICALIZATION: {"method": "none"}, CODE: "h2", DIGEST_ALGORITHM: "sha256",
            HASH_STANDARD: "raw", HASH_VERSION: 1,
            ID: "tag:simson.net,2026:mailarchiver/hash/raw/v1/sha256",
            INPUT: "recovered-rfc5322-message", SCOPE: "message", TYPE: "hash-standard",
        },
        {
            CANONICALIZATION: SEMANTIC_CANONICALIZATION, CODE: "h3", DIGEST_ALGORITHM: "sha256",
            HASH_STANDARD: "semantic", HASH_VERSION: 1,
            ID: "tag:simson.net,2026:mailarchiver/hash/semantic/v1/sha256",
            INPUT: "recovered-rfc5322-message", SCOPE: "message", TYPE: "hash-standard",
        },
    ]


def write_integrity_file(
    mbox_path: Path,
    integrity_path: Path,
    messages: Iterable[IntegrityMessage],
    message_count: int,
) -> str:
    """Atomically write the current integrity format and return the MBOX SHA-256."""
    initial_stat = mbox_path.stat()
    initial_identity = (
        initial_stat.st_dev, initial_stat.st_ino, initial_stat.st_size,
        initial_stat.st_mtime_ns, initial_stat.st_ctime_ns,
    )
    mbox_sha256 = _digest_file(mbox_path, ("sha256",))["sha256"]
    current = mbox_path.stat()
    if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != initial_identity:
        raise ValueError(f"MBOX changed while hashing: {mbox_path}")
    mbox_token = f"h1:{mbox_sha256}"
    records: list[dict[str, object]] = [
        {FORMAT_ID_KEY: FORMAT_ID, FORMAT_VERSION_KEY: FORMAT_VERSION,
         MANIFEST_ID: mbox_token, TYPE: "integrity-manifest"},
        *_standard_records(),
        {BYTES: initial_stat.st_size, HASHES: [mbox_token], MESSAGES: message_count,
         NAME: mbox_path.name, TYPE: "mbox"},
        {COLUMNS: ["ordinal", "message-id-json", "hashes..."], ENCODING: "tsv",
         TYPE: "message-table"},
    ]
    destination = integrity_path
    temporary = destination.with_name(f".{destination.name}.tmp")
    written = 0
    try:
        with temporary.open("wb") as output:
            for record in records:
                output.write(_json_bytes(record))
            output.write(f"{TABLE_HEADER}\n".encode("ascii"))
            for written, message in enumerate(messages, 1):
                actual_raw = hashlib.sha256(message.raw).hexdigest()
                if actual_raw != message.raw_sha256:
                    raise ValueError(f"raw SHA-256 mismatch for message {message.message_id}")
                semantic = hashlib.sha256(semantic_bytes(message.raw)).hexdigest()
                message_id = json.dumps(message.message_id, ensure_ascii=True, separators=(",", ":"))
                output.write(f"{written}\t{message_id}\th2:{actual_raw}\th3:{semantic}\n".encode())
            if written != message_count:
                raise ValueError(f"expected {message_count} messages, received {written}")
            current = mbox_path.stat()
            current_identity = (
                current.st_dev, current.st_ino, current.st_size,
                current.st_mtime_ns, current.st_ctime_ns,
            )
            if current_identity != initial_identity or _digest_file(mbox_path, ("sha256",))["sha256"] != mbox_sha256:
                raise ValueError(f"MBOX changed while writing integrity file: {mbox_path}")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(destination)
        _sync_directory(destination.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return mbox_sha256


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_json(line: bytes) -> dict[str, object]:
    if not line.endswith(b"\n") or line.startswith(b"\xef\xbb\xbf"):
        raise ValueError("control records must be UTF-8 lines terminated by LF")
    def object_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate JSON object key")
        return dict(pairs)

    value = json.loads(line, object_pairs_hook=object_hook)
    if not isinstance(value, dict):
        raise ValueError("control record must be a JSON object")
    if _json_bytes(value) != line:
        raise ValueError("control record is not deterministically encoded")
    return value


def _parse_standard(record: dict[str, object], prior: list[HashStandard]) -> HashStandard:
    code = record.get(CODE)
    algorithm = record.get(DIGEST_ALGORITHM)
    standard = record.get(HASH_STANDARD)
    version = record.get(HASH_VERSION)
    scope = record.get(SCOPE)
    same_input_as = record.get(SAME_INPUT_AS)
    if code != f"h{len(prior) + 1}" or not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
        raise ValueError("hash codes must be consecutive h1, h2, ...")
    if algorithm not in ALGORITHMS:
        raise ValueError(f"unsupported digest algorithm for {code}: {algorithm}")
    if standard not in {"mbox", "raw", "semantic"} or version != 1 or scope not in {"mbox", "message"}:
        raise ValueError(f"unsupported hash standard for {code}")
    if scope != ("mbox" if standard == "mbox" else "message"):
        raise ValueError(f"invalid scope for {code}")
    expected_id = f"tag:simson.net,2026:mailarchiver/hash/{standard}/v{version}/{algorithm}"
    if record.get(ID) != expected_id:
        raise ValueError(f"invalid global hash-standard id for {code}")
    expected_input = "complete-mbox-file" if standard == "mbox" else "recovered-rfc5322-message"
    if same_input_as is None:
        expected_canonicalization = SEMANTIC_CANONICALIZATION if standard == "semantic" else {"method": "none"}
        if record.get(INPUT) != expected_input or record.get(CANONICALIZATION) != expected_canonicalization:
            raise ValueError(f"unsupported canonicalization for {code}")
    else:
        referenced = next((item for item in prior if item.code == same_input_as), None)
        if (referenced is None or referenced.same_input_as is not None or referenced.standard != standard
                or referenced.version != version or referenced.scope != scope):
            raise ValueError(f"invalid same_input_as for {code}")
    return HashStandard(code, str(algorithm), str(standard), int(version), str(scope),
                        str(same_input_as) if same_input_as else None)


def _parse_token(token: object, standards: list[HashStandard]) -> tuple[HashStandard, str]:
    if not isinstance(token, str):
        raise ValueError("hash token must be a string")
    code, separator, digest = token.partition(":")
    standard = next((item for item in standards if item.code == code), None)
    if not separator or standard is None or not HEX_PATTERN.fullmatch(digest):
        raise ValueError(f"invalid hash token: {token}")
    if len(digest) != ALGORITHMS[standard.algorithm]:
        raise ValueError(f"wrong digest length for {code}")
    return standard, digest


def _stored_candidates(box: mailbox.mbox, key: object) -> Iterable[bytes]:
    raw = box.get_bytes(key, from_=False)
    lines = raw.splitlines(keepends=True)
    ambiguous = [index for index, line in enumerate(lines) if line.startswith(b">From ")]
    fully_unquoted = (1 << len(ambiguous)) - 1
    masks = [fully_unquoted] + ([0] if fully_unquoted else [])
    if len(ambiguous) <= MAX_AMBIGUOUS_FROM_LINES:
        masks.extend(range(1, fully_unquoted))
    for mask in masks:
        candidate = list(lines)
        for bit, index in enumerate(ambiguous):
            if mask & (1 << bit):
                candidate[index] = candidate[index][1:]
        yield b"".join(candidate)
    if raw == b"\n":
        yield b""


def _check_hashes(tokens: list[str], standards: list[HashStandard], data: bytes) -> list[str]:
    errors: list[str] = []
    parsed = [_parse_token(token, standards) for token in tokens]
    if [standard.code for standard, _ in parsed] != [standard.code for standard in standards]:
        raise ValueError("hash tokens do not match declared codes in order")
    semantic: bytes | None = None
    for standard, expected in parsed:
        if standard.standard == "semantic":
            semantic = semantic if semantic is not None else semantic_bytes(data)
            actual = standard.digest(semantic)
        else:
            actual = standard.digest(data)
        if actual != expected:
            errors.append(f"{standard.code} mismatch: expected {expected}, found {actual}")
    return errors


def verify_mbox(path: Path, integrity: Path) -> list[str]:
    if not integrity.is_file():
        return [f"{path.name}: missing integrity file {integrity.relative_to(integrity.parents[1])}"]
    try:
        with integrity.open("rb") as source:
            manifest = _load_json(source.readline())
            if (manifest.get(TYPE) != "integrity-manifest" or manifest.get(FORMAT_ID_KEY) != FORMAT_ID
                    or manifest.get(FORMAT_VERSION_KEY) != FORMAT_VERSION):
                raise ValueError("unsupported integrity manifest")
            standards: list[HashStandard] = []
            record = _load_json(source.readline())
            while record.get(TYPE) == "hash-standard":
                standards.append(_parse_standard(record, standards))
                record = _load_json(source.readline())
            required = [("h1", "sha256", "mbox"), ("h2", "sha256", "raw"), ("h3", "sha256", "semantic")]
            if [(item.code, item.algorithm, item.standard) for item in standards[:3]] != required:
                raise ValueError("required h1, h2, and h3 standards are not declared")
            if record.get(TYPE) != "mbox" or record.get(NAME) != path.name:
                raise ValueError("invalid MBOX record")
            mbox_record = record
            table = _load_json(source.readline())
            expected_table = {COLUMNS: ["ordinal", "message-id-json", "hashes..."],
                              ENCODING: "tsv", TYPE: "message-table"}
            if table != expected_table:
                raise ValueError("invalid message-table record")
            if source.readline() != f"{TABLE_HEADER}\n".encode("ascii"):
                raise ValueError("invalid TSV header")

            errors: list[str] = []
            if path.stat().st_size != mbox_record.get(BYTES):
                errors.append(f"{path.name}: byte count mismatch")
            mbox_standards = [item for item in standards if item.scope == "mbox"]
            mbox_tokens = mbox_record.get(HASHES)
            if not isinstance(mbox_tokens, list) or not mbox_tokens:
                raise ValueError("invalid MBOX hashes")
            parsed_mbox = [_parse_token(token, standards) for token in mbox_tokens]
            if [item.code for item, _ in parsed_mbox] != [item.code for item in mbox_standards]:
                raise ValueError("MBOX hashes do not match declared codes")
            file_digests = _digest_file(path, {item.algorithm for item in mbox_standards})
            for standard, expected in parsed_mbox:
                actual = file_digests[standard.algorithm]
                if actual != expected:
                    errors.append(f"{path.name}: {standard.code} mismatch: expected {expected}, found {actual}")
            if manifest.get(MANIFEST_ID) != mbox_tokens[0]:
                raise ValueError("manifest_id is not the primary MBOX hash")

            message_standards = [item for item in standards if item.scope == "message"]
            raw_standards = [item for item in message_standards if item.standard == "raw"]
            if not raw_standards:
                raise ValueError("no raw-message standard declared")
            box = mailbox.mbox(path, factory=None, create=False)
            rows = 0
            try:
                keys = iter(box.iterkeys())
                for rows, line in enumerate(source, 1):
                    if not line.endswith(b"\n"):
                        raise ValueError(f"TSV row {rows} is not LF terminated")
                    fields = line[:-1].decode("utf-8").split("\t")
                    if len(fields) != 2 + len(message_standards) or fields[0] != str(rows):
                        raise ValueError(f"invalid TSV message row {rows}")
                    message_id = json.loads(fields[1])
                    if message_id is not None and not isinstance(message_id, str):
                        raise ValueError(f"invalid message-id-json in row {rows}")
                    if json.dumps(message_id, ensure_ascii=True, separators=(",", ":")) != fields[1]:
                        raise ValueError(f"message-id-json is not deterministically encoded in row {rows}")
                    parsed = [_parse_token(token, standards) for token in fields[2:]]
                    if [item.code for item, _ in parsed] != [item.code for item in message_standards]:
                        raise ValueError(f"message hashes do not match declared codes in row {rows}")
                    try:
                        key = next(keys)
                    except StopIteration:
                        errors.append(f"{path.name}: sidecar contains more messages than MBOX")
                        break
                    expected_raw = {item.code: digest for item, digest in parsed if item.standard == "raw"}
                    candidate = next((raw for raw in _stored_candidates(box, key)
                                      if all(item.digest(raw) == expected_raw[item.code]
                                             for item in raw_standards)), None)
                    if candidate is None:
                        errors.append(f"{path.name}: message {rows} raw hash mismatch")
                        continue
                    for detail in _check_hashes(fields[2:], message_standards, candidate):
                        errors.append(f"{path.name}: message {rows} {detail}")
                if next(keys, None) is not None:
                    errors.append(f"{path.name}: MBOX contains more messages than sidecar")
            finally:
                box.close()
            if rows != mbox_record.get(MESSAGES):
                errors.append(f"{path.name}: message count mismatch")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, mailbox.Error) as error:
        return [f"{integrity.name}: {error}"]
    if not errors:
        print(f"OK {path.name}: {rows} messages")
    return errors


def _decode_manifest_path(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        code = match.group(1).lower()
        if code not in {"0a", "0d", "25"}:
            raise ValueError(f"invalid BagIt pathname escape %{match.group(1)}")
        return {"0a": "\n", "0d": "\r", "25": "%"}[code]

    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("invalid BagIt pathname escape")
    return re.sub(r"%([0-9A-Fa-f]{2})", replace, value)


def _safe_manifest_target(archive: Path, value: str) -> tuple[str, Path]:
    decoded = _decode_manifest_path(value)
    logical = PurePosixPath(decoded)
    if logical.is_absolute() or not logical.parts or any(part in {"", ".", ".."} for part in logical.parts):
        raise ValueError(f"unsafe BagIt pathname: {value}")
    target = archive.joinpath(*logical.parts)
    component = archive
    for part in logical.parts:
        component /= part
        if component.is_symlink():
            raise ValueError(f"BagIt manifests may not reference a symlink: {value}")
    try:
        target.resolve(strict=True).relative_to(archive.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise ValueError(f"missing or unsafe BagIt pathname: {value}") from error
    return logical.as_posix(), target


def _verify_bagit_manifest(path: Path, archive: Path, payload: bool) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    declared: set[str] = set()
    try:
        data = path.read_bytes()
        if b"\r" in data or (data and not data.endswith(b"\n")):
            raise ValueError("manifest must use LF-terminated records")
        for ordinal, raw_line in enumerate(data.splitlines(), 1):
            line = raw_line.decode("utf-8")
            fields = line.split(None, 1)
            if len(fields) != 2 or not SHA256_PATTERN.fullmatch(fields[0]):
                raise ValueError(f"invalid manifest row {ordinal}")
            logical, target = _safe_manifest_target(archive, fields[1])
            if payload != logical.startswith("data/"):
                raise ValueError(f"row {ordinal} has the wrong manifest scope: {logical}")
            if logical in declared:
                raise ValueError(f"duplicate manifest pathname: {logical}")
            declared.add(logical)
            actual = _digest_file(target, ("sha256",))["sha256"]
            if actual.lower() != fields[0].lower():
                errors.append(f"{path.name}: SHA-256 mismatch for {logical}: expected {fields[0]}, found {actual}")
    except (OSError, UnicodeError, ValueError) as error:
        return [f"{path.name}: {error}"], declared
    return errors, declared


def _bag_info(archive: Path, payloads: list[Path]) -> list[str]:
    path = archive / "bag-info.txt"
    try:
        fields: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            label, separator, value = line.partition(": ")
            if not separator or not label or label in fields:
                raise ValueError("invalid or duplicate metadata field")
            fields[label] = value
        required = {
            "Bag-Type": "Mailbag",
            "Mailbag-Source": "mbox",
            "Mailbag-Specification-Version": "1.0",
            "Original-Included": "False",
            "Mailbag-Agent": "mailarchiver",
        }
        for label, expected in required.items():
            if fields.get(label) != expected:
                raise ValueError(f"invalid {label}")
        for label in ("Bagging-Timestamp", "Bagging-Date", "External-Identifier", "Mailbag-Agent-Version"):
            if not fields.get(label):
                raise ValueError(f"missing {label}")
        packaged = datetime.fromisoformat(fields["Bagging-Timestamp"])
        if packaged.tzinfo is None or packaged.date().isoformat() != fields["Bagging-Date"]:
            raise ValueError("inconsistent Bagging-Timestamp and Bagging-Date")
        expected_oxum = f"{sum(item.stat().st_size for item in payloads)}.{len(payloads)}"
        if fields.get("Payload-Oxum") != expected_oxum:
            raise ValueError(f"Payload-Oxum mismatch: expected {expected_oxum}")
    except (OSError, UnicodeError, ValueError) as error:
        return [f"bag-info.txt: {error}"]
    return []


def _mailbag_csv_paths(archive: Path) -> list[Path]:
    single = archive / "mailbag.csv"
    split = []
    for path in archive.glob("mailbag-*.csv"):
        match = SPLIT_CSV_PATTERN.fullmatch(path.name)
        if match:
            split.append((int(match.group(1)), path))
    split.sort()
    if single.is_file() and split:
        raise ValueError("both mailbag.csv and split mailbag CSV files are present")
    if single.is_file():
        return [single]
    if not split or [index for index, _ in split] != list(range(1, len(split) + 1)):
        raise ValueError("missing or noncontiguous Mailbag CSV files")
    width = len(str(len(split)))
    if any(path.name != f"mailbag-{index:0{width}d}.csv" for index, path in split):
        raise ValueError("Mailbag CSV numbering has inconsistent zero padding")
    return [path for _, path in split]


def _verify_mailbag_csv(
    archive: Path,
    expected_mailboxes: dict[str, int],
) -> tuple[list[str], list[Path]]:
    try:
        paths = _mailbag_csv_paths(archive)
    except ValueError as error:
        return [str(error)], []
    identifiers: set[str] = set()
    references: dict[str, int] = {}
    rows = 0
    try:
        for file_index, path in enumerate(paths):
            data = path.read_bytes()
            remainder = data.replace(b"\r\n", b"")
            if data and (not data.endswith(b"\r\n") or b"\n" in remainder or b"\r" in remainder):
                raise ValueError(f"{path.name} must use CRLF records")
            records = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
            if file_index == 0:
                header = next(records, None)
                if tuple(header or ()) != MAILBAG_HEADERS:
                    raise ValueError(f"{path.name} has an invalid header")
            file_rows = 0
            for record in records:
                file_rows += 1
                rows += 1
                if len(record) != len(MAILBAG_HEADERS):
                    raise ValueError(f"{path.name} row {rows} has the wrong field count")
                identifier = record[1]
                if (not MAILBAG_ID_PATTERN.fullmatch(identifier) or len(identifier) > 36
                        or identifier.casefold() in identifiers):
                    raise ValueError(f"invalid or duplicate Mailbag-Message-ID: {identifier}")
                identifiers.add(identifier.casefold())
                original = PurePosixPath(record[3])
                if (original.is_absolute() or len(original.parts) != 1
                        or any(part in {"", ".", ".."} for part in original.parts)):
                    raise ValueError(f"Mailbag row has unsafe MBOX path: {record[3]}")
                if not (archive / "data" / "mbox").joinpath(*original.parts).is_file():
                    raise ValueError(f"Mailbag row references missing MBOX: {record[3]}")
                references[original.name] = references.get(original.name, 0) + 1
                if not record[6].isdigit():
                    raise ValueError(f"Mailbag row has invalid attachment count: {record[6]}")
            if file_rows > MAILBAG_ROW_LIMIT:
                raise ValueError(f"{path.name} exceeds {MAILBAG_ROW_LIMIT} message rows")
            if len(paths) > 1 and file_index < len(paths) - 1 and file_rows != MAILBAG_ROW_LIMIT:
                raise ValueError(f"{path.name} must contain {MAILBAG_ROW_LIMIT} message rows")
            if len(paths) > 1 and file_rows == 0:
                raise ValueError(f"{path.name} has no message rows")
        expected_messages = sum(expected_mailboxes.values())
        if rows != expected_messages:
            raise ValueError(f"Mailbag CSV has {rows} messages; expected {expected_messages}")
        if references != expected_mailboxes:
            raise ValueError("Mailbag CSV message counts do not match their MBOX containers")
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        return [str(error)], paths
    return [], paths


def _payload_files(archive: Path) -> tuple[list[Path], list[str]]:
    data = archive / "data"
    if not data.is_dir():
        return [], ["missing BagIt payload directory data/"]
    files: list[Path] = []
    errors: list[str] = []
    for path in sorted(data.rglob("*")):
        if path.is_symlink():
            errors.append(f"payload symlink is not supported: {path.relative_to(archive)}")
        elif path.is_file():
            files.append(path)
    return files, errors


def verify_archive(archive: Path) -> list[str]:
    errors: list[str] = []
    legacy = sorted((*archive.glob("*.mbox"), *archive.glob("*.mbox.integrity")))
    if legacy:
        errors.append(
            "unsupported root-level legacy archive output: "
            + ", ".join(path.name for path in legacy)
        )
    declaration = archive / "bagit.txt"
    try:
        if declaration.read_bytes() != BAGIT_DECLARATION:
            errors.append("bagit.txt: unsupported BagIt declaration")
    except OSError as error:
        errors.append(f"bagit.txt: {error}")

    supported_manifests = {PAYLOAD_MANIFEST, TAG_MANIFEST}
    additional_manifests = sorted(
        path.name
        for pattern in ("manifest-*.txt", "tagmanifest-*.txt")
        for path in archive.glob(pattern)
        if path.name not in supported_manifests
    )
    if additional_manifests:
        errors.append(f"unsupported additional BagIt manifests: {', '.join(additional_manifests)}")

    payloads, payload_errors = _payload_files(archive)
    errors.extend(payload_errors)
    payload_manifest = archive / PAYLOAD_MANIFEST
    payload_hash_errors, declared_payloads = _verify_bagit_manifest(payload_manifest, archive, True)
    errors.extend(payload_hash_errors)
    actual_payloads = {path.relative_to(archive).as_posix() for path in payloads}
    if declared_payloads != actual_payloads:
        missing = sorted(actual_payloads - declared_payloads)
        extra = sorted(declared_payloads - actual_payloads)
        if missing:
            errors.append(f"{PAYLOAD_MANIFEST}: missing payload entries: {', '.join(missing)}")
        if extra:
            errors.append(f"{PAYLOAD_MANIFEST}: nonexistent payload entries: {', '.join(extra)}")

    mailboxes = sorted((archive / "data" / "mbox").glob("*.mbox"))
    native_payloads = {path.relative_to(archive).as_posix() for path in mailboxes}
    unexpected_payloads = sorted(actual_payloads - native_payloads)
    if unexpected_payloads:
        errors.append(f"unsupported native archive payloads: {', '.join(unexpected_payloads)}")
    for path in mailboxes:
        errors.extend(verify_mbox(path, archive / "integrity" / f"{path.name}{INTEGRITY_SUFFIX}"))
    mailbox_names = {path.name for path in mailboxes}
    for integrity in sorted((archive / "integrity").glob(f"*.mbox{INTEGRITY_SUFFIX}")):
        if integrity.name.removesuffix(INTEGRITY_SUFFIX) not in mailbox_names:
            errors.append(f"{integrity.name}: integrity file has no MBOX file")

    expected_mailboxes: dict[str, int] = {}
    for path in mailboxes:
        box = mailbox.mbox(path, factory=None, create=False)
        try:
            expected_mailboxes[path.name] = len(box)
        finally:
            box.close()
    csv_errors, csv_paths = _verify_mailbag_csv(archive, expected_mailboxes)
    errors.extend(csv_errors)
    errors.extend(_bag_info(archive, payloads))

    tag_manifest = archive / TAG_MANIFEST
    tag_hash_errors, declared_tags = _verify_bagit_manifest(tag_manifest, archive, False)
    errors.extend(tag_hash_errors)
    if TAG_MANIFEST in declared_tags:
        errors.append(f"{TAG_MANIFEST}: a tag manifest must not list itself")
    required_tags = {
        "bagit.txt",
        "bag-info.txt",
        PAYLOAD_MANIFEST,
        *(path.relative_to(archive).as_posix() for path in csv_paths),
        *(f"integrity/{path.name}{INTEGRITY_SUFFIX}" for path in mailboxes),
    }
    verifier = archive / INSTALLED_NAME
    if verifier.is_file():
        required_tags.add(INSTALLED_NAME)
    missing_tags = sorted(required_tags - declared_tags)
    if missing_tags:
        errors.append(f"{TAG_MANIFEST}: missing tag entries: {', '.join(missing_tags)}")
    return errors


def install_archive_verifier(archive: Path) -> Path:
    """Install this dependency-free source file in an archive atomically."""
    source = Path(__file__).read_bytes()
    destination = archive / INSTALLED_NAME
    if destination.is_symlink():
        raise ValueError(f"archive verifier may not be a symlink: {destination}")
    if destination.is_file() and destination.read_bytes() == source:
        return destination
    temporary = archive / f".{INSTALLED_NAME}.tmp"
    temporary.write_bytes(source)
    os.chmod(temporary, 0o755)
    temporary.replace(destination)
    _sync_directory(archive)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate BagIt, Mailbag, whole-MBOX, raw-message, and semantic-message hashes."
    )
    parser.add_argument("archive", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    archive = parser.parse_args().archive
    if not archive.is_dir():
        parser.error(f"not a directory: {archive}")
    errors = verify_archive(archive)
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if errors:
        print(f"FAILED: {len(errors)} integrity error(s)", file=sys.stderr)
        return 1
    print("Archive integrity verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
