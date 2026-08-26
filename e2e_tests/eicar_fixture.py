"""Create an EICAR-bearing EMLX only inside a disposable test directory."""

from __future__ import annotations

import base64
from pathlib import Path


EICAR_PIECES = (
    b"X5O!P%@",
    b"AP[4\\PZX54(P^)",
    b"7CC)7}$EI",
    b"CAR-STANDARD-",
    b"ANTIVIRUS-TEST-",
    b"FILE!$H+H*",
)


def write_eicar_emlx(template: Path, destination: Path) -> bytes:
    """Materialize a valid EMLX whose attachment is assembled only at runtime."""
    trigger = b"".join(EICAR_PIECES)
    encoded = base64.b64encode(trigger)
    message = template.read_bytes().replace(b"{{EICAR_BASE64}}", encoded)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(str(len(message)).encode("ascii") + b"\n" + message)
    return message
