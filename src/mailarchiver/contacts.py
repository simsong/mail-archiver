# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Read-only address-level Contact queries over an archive catalog."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field
from tabulate import tabulate

from .contact_filtering import ContactKind, classify_address, contact_filters


class ContactRow(BaseModel):
    """One address-level Contact and its all-header appearance statistics."""

    address: str
    first_seen: str
    last_seen: str
    message_count: int = Field(ge=1)


def load_owner_addresses(path: Path) -> tuple[str, ...]:
    """Read owner aliases separated by newlines, commas, or semicolons."""
    content = "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    return tuple(
        sorted(
            {
                value.strip().lower()
                for value in re.split(r"[,;\n]", content)
                if value.strip()
            }
        )
    )


def _connection(archive: Path) -> sqlite3.Connection:
    path = (archive / "archive.sqlite3").resolve()
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def _owners(database: sqlite3.Connection, addresses: tuple[str, ...], aliases: tuple[str, ...]) -> tuple[int, ...]:
    exact = tuple(sorted({address.strip().lower() for address in addresses if address.strip()}))
    aliases = tuple(sorted({alias.strip().lower() for alias in aliases if alias.strip()}))
    if not exact and not aliases:
        raise ValueError("--owner-address-file or --owner-address is required unless --all is selected")
    direct: tuple[int, ...] = ()
    present: set[str] = set()
    if exact:
        placeholders = ", ".join("?" for _ in exact)
        matches = tuple(
            (int(row[0]), str(row[1]))
            for row in database.execute(
                f"SELECT address_pk, lower(address) FROM email_addresses WHERE lower(address) IN ({placeholders})", exact
            )
        )
        direct = tuple(address_pk for address_pk, _ in matches)
        present = {address for _, address in matches}
    missing = sorted(set(exact) - present)
    if missing:
        raise ValueError(f"owner address is not present in this archive: {', '.join(missing)}")
    matched_aliases: tuple[int, ...] = ()
    if aliases:
        patterns = tuple("%" + alias.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for alias in aliases)
        predicate = " OR ".join("lower(email_addresses.address) LIKE ? ESCAPE '\\'" for _ in patterns)
        matched_aliases = tuple(
            int(row[0])
            for row in database.execute(
                "SELECT DISTINCT email_addresses.address_pk FROM email_addresses "
                "JOIN messages ON messages.sender_address_pk = email_addresses.address_pk "
                f"WHERE messages.category = 'Sent' AND ({predicate})",
                patterns,
            )
        )
    owners = tuple(sorted(set(direct) | set(matched_aliases)))
    if not owners:
        raise ValueError("owner aliases did not match a Sent sender address in this archive")
    return owners


def contacts(
    archive: Path,
    *,
    owner_addresses: tuple[str, ...] = (),
    owner_aliases: tuple[str, ...] = (),
    meaningful_only: bool = True,
) -> list[ContactRow]:
    """Return Contacts with all-header statistics and an optional direct-contact filter."""
    database = _connection(archive)
    try:
        filters = contact_filters(archive)
        owner_pks = _owners(database, owner_addresses, owner_aliases) if meaningful_only else ()
        owner_cte = ""
        parameters: tuple[int, ...] = ()
        meaningful_join = ""
        where_clause = ""
        if meaningful_only:
            placeholders = ", ".join("?" for _ in owner_pks)
            owner_cte = f"""
                owners AS (
                    SELECT address_pk FROM email_addresses WHERE address_pk IN ({placeholders})
                ),
                meaningful AS (
                    SELECT messages.message_pk, recipients.address_pk
                    FROM messages JOIN recipients USING (message_pk)
                    WHERE messages.sender_address_pk IN (SELECT address_pk FROM owners)
                      AND recipients.role IN ('to', 'bcc')
                    UNION
                    SELECT messages.message_pk, messages.sender_address_pk
                    FROM messages JOIN recipients USING (message_pk)
                    WHERE recipients.role = 'to'
                      AND recipients.address_pk IN (SELECT address_pk FROM owners)
                      AND messages.sender_address_pk NOT IN (SELECT address_pk FROM owners)
                ),
                meaningful_addresses AS (
                    SELECT DISTINCT address_pk FROM meaningful
                ),
            """
            parameters = owner_pks
            meaningful_join = """
                JOIN meaningful_addresses USING (address_pk)
            """
            where_clause = """
                WHERE appearances.address_pk NOT IN (SELECT address_pk FROM owners)
            """
        rows = database.execute(
            f"""
            WITH {owner_cte}
            appearances AS (
                SELECT message_pk, date_utc, sender_address_pk AS address_pk FROM messages
                UNION
                SELECT messages.message_pk, messages.date_utc, recipients.address_pk
                FROM messages JOIN recipients USING (message_pk)
            )
            SELECT email_addresses.address,
                   MIN(substr(appearances.date_utc, 1, 10)),
                   MAX(substr(appearances.date_utc, 1, 10)),
                   COUNT(*)
            FROM appearances
            {meaningful_join}
            JOIN email_addresses ON email_addresses.address_pk = appearances.address_pk
            {where_clause}
            GROUP BY email_addresses.address
            ORDER BY COUNT(*) DESC, lower(email_addresses.address), email_addresses.address
            """,
            parameters,
        )
        return [
            ContactRow(address=str(address), first_seen=str(first_seen), last_seen=str(last_seen), message_count=int(count))
            for address, first_seen, last_seen, count in rows
            if classify_address(str(address), filters).kind is ContactKind.HUMAN
        ]
    finally:
        database.close()


def print_contacts(rows: list[ContactRow], output_format: str) -> None:
    """Render Contact rows for people or scripts without writing the archive."""
    if output_format == "json":
        print(json.dumps([row.model_dump() for row in rows], indent=2))
        return
    if output_format == "tsv":
        print("address\tfirst_seen\tlast_seen\tmessages")
        for row in rows:
            print(f"{row.address}\t{row.first_seen}\t{row.last_seen}\t{row.message_count}")
        return
    print(
        tabulate(
            ((row.address, row.first_seen, row.last_seen, row.message_count) for row in rows),
            headers=("address", "first", "last", "messages"),
            tablefmt="simple",
            intfmt=",",
            colalign=("left", "left", "left", "right"),
        )
    )
