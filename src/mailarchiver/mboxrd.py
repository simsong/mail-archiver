"""Reversible MBOXRD storage quoting; inputs exclude the record separator."""

import re

_FROM_LINE = re.compile(br"(?m)^(?=>*From )")
_QUOTED_FROM_LINE = re.compile(br"(?m)^>(?=>*From )")


def quote(raw: bytes) -> bytes:
    """Add one greater-than sign to every From-like payload line."""
    return _FROM_LINE.sub(b">", raw)


def unquote(stored: bytes) -> bytes:
    """Remove exactly one storage quote, retaining original quote depth."""
    return _QUOTED_FROM_LINE.sub(b"", stored)
