# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""High-precision, non-LLM signature-block evidence extraction."""

from __future__ import annotations

import re
import unicodedata

from .models import SignatureBlock, SignatureFact, SignatureFactKind, SignatureMethod


EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()]+", re.IGNORECASE)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{5,}\d)(?!\w)")
DELIMITER = re.compile(r"^\s*(?:--+|__+|==+)\s*$")
QUOTED_BOUNDARY = re.compile(
    r"^(?:>+\s?|on .{0,160}wrote:|from:\s|sent:\s|-----\s*original message\s*-----)", re.IGNORECASE
)
MOBILE_FOOTER = re.compile(r"^(?:sent from|get outlook for|envoy[eé] de mon)\b", re.IGNORECASE)
SIGNOFF = re.compile(
    r"^(?:best|best regards|cheers|cordially|kind regards|many thanks|regards|sincerely|thanks|thank you)[,!]?$",
    re.IGNORECASE,
)
CONTACT_WORD = re.compile(
    r"\b(?:fax|mobile|office|phone|tel|telephone|www|linkedin|department|university|institute|corporation|company)\b",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    """Return a conservative comparison form while preserving source text elsewhere."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_phone(value: str) -> str:
    prefix = "+" if value.lstrip().startswith("+") else ""
    return prefix + "".join(character for character in value if character.isdigit())


def _has_contact_anchor(line: str) -> bool:
    phones = [match.group(0) for match in PHONE.finditer(line)]
    return bool(EMAIL.search(line) or URL.search(line) or any(len(normalize_phone(phone).lstrip("+")) >= 7 for phone in phones))


def _candidate_name(line: str) -> bool:
    stripped = line.strip(" ,")
    tokens = stripped.replace(".", "").replace("'", "").replace("-", " ").split()
    return (
        1 <= len(tokens) <= 5
        and len(stripped) <= 60
        and all(token.isalpha() for token in tokens)
        and not SIGNOFF.fullmatch(stripped)
        and not CONTACT_WORD.search(stripped)
    )

def _visible_lines(body: str) -> list[tuple[int, str]]:
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    boundary = next((index for index, line in enumerate(lines) if QUOTED_BOUNDARY.match(line.strip())), len(lines))
    return [(index, line.rstrip()) for index, line in enumerate(lines[:boundary])]


def _last_paragraph(lines: list[tuple[int, str]], anchor_index: int) -> list[tuple[int, str]]:
    start = anchor_index
    while start > 0 and lines[start - 1][1].strip() and anchor_index - start < 11:
        start -= 1
    end = anchor_index + 1
    while end < len(lines) and lines[end][1].strip() and end - start < 12:
        end += 1
    block = lines[start:end]
    if start >= 2 and SIGNOFF.fullmatch(lines[start - 2][1].strip()) and not lines[start - 1][1].strip():
        block = [lines[start - 2], *block]
    return block


def _select_block(lines: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], SignatureMethod, float] | None:
    nonempty = [item for item in lines if item[1].strip()]
    if not nonempty:
        return None
    tail_start = max(0, len(lines) - 30)
    for index in range(len(lines) - 1, tail_start - 1, -1):
        if DELIMITER.fullmatch(lines[index][1]):
            block = [item for item in lines[index + 1 :] if item[1].strip()][:12]
            return (block, SignatureMethod.DELIMITER, 0.98) if block else None
    for index in range(len(lines) - 1, tail_start - 1, -1):
        if MOBILE_FOOTER.match(lines[index][1].strip()):
            return [lines[index]], SignatureMethod.MOBILE_FOOTER, 0.97
    for index in range(len(lines) - 1, tail_start - 1, -1):
        if _has_contact_anchor(lines[index][1]):
            return _last_paragraph(lines, index), SignatureMethod.CONTACT_BLOCK, 0.88
    for index in range(max(tail_start, len(lines) - 6), len(lines)):
        if SIGNOFF.fullmatch(lines[index][1].strip()):
            block = [item for item in lines[index : index + 4] if item[1].strip()]
            if len(block) >= 2 and _candidate_name(block[1][1]):
                return block, SignatureMethod.SIGNOFF, 0.64
    return None


def _facts(block: list[tuple[int, str]], confidence: float) -> list[SignatureFact]:
    facts: list[SignatureFact] = []
    name_recorded = False

    def add(line_ordinal: int, kind: SignatureFactKind, value: str, normalized: str, fact_confidence: float) -> None:
        facts.append(
            SignatureFact(
                ordinal=len(facts),
                line_ordinal=line_ordinal,
                kind=kind,
                value=value,
                normalized_value=normalized,
                confidence=fact_confidence,
            )
        )

    for line_ordinal, (_, line) in enumerate(block):
        stripped = line.strip()
        if not stripped:
            continue
        add(line_ordinal, SignatureFactKind.TEXT, stripped, normalize_text(stripped), confidence)
        for match in EMAIL.finditer(stripped):
            add(line_ordinal, SignatureFactKind.EMAIL, match.group(0), match.group(0).casefold(), confidence)
        for match in URL.finditer(stripped):
            add(line_ordinal, SignatureFactKind.URL, match.group(0), match.group(0).casefold().rstrip(".,"), confidence)
        for match in PHONE.finditer(stripped):
            normalized = normalize_phone(match.group(0))
            if len(normalized.lstrip("+")) >= 7:
                add(line_ordinal, SignatureFactKind.PHONE, match.group(0), normalized, confidence * 0.95)
        if not name_recorded and line_ordinal < 4 and _candidate_name(stripped):
            add(line_ordinal, SignatureFactKind.NAME_CANDIDATE, stripped, normalize_text(stripped), confidence * 0.70)
            name_recorded = True
    return facts


def extract_signature(body: str) -> SignatureBlock | None:
    """Extract one conservative bottom-of-message signature candidate.

    The result is evidence, not an assertion that a name or contact identifies
    the sender. Corpus-level repetition and later matching stages can strengthen
    or reject it.
    """
    lines = _visible_lines(body)
    selected = _select_block(lines)
    if selected is None:
        return None
    block, method, confidence = selected
    if not block:
        return None
    text = "\n".join(line for _, line in block)
    return SignatureBlock(
        start_line=block[0][0],
        end_line=block[-1][0],
        method=method,
        confidence=confidence,
        text=text,
        facts=_facts(block, confidence),
    )
