"""Print compact aggregates from the generated evidence tables."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def main() -> None:
    root = Path(sys.argv[1])
    bad = rows(root / "BAD_DATES.csv")
    missing = rows(root / "MISSING_SENDER.csv")
    print("bad dates without plausible Received:")
    for row in bad:
        if not row["plausible_received_date"]:
            print(row["message_pk"], row["catalog_date"], repr(row["raw_date"]), row["subject"], row["source_paths"])
    print("bad-date replacement years:", Counter(row["plausible_received_date"][:4] or "none" for row in bad))
    print("sample source-envelope senders:", Counter(row["source_boundary_sender"] or "none" for row in missing))
    print("sample source files:", Counter(row["source_paths"] for row in missing))


if __name__ == "__main__":
    main()
