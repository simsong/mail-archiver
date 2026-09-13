<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Contacts and geography design

Only the read-only `human-contacts` CLI and filtering policy are implemented.
The Contacts window, geographic database, updater, and identity resolution below
are planned work, not shipped features.

## Scope and terminology

A **Contact** is one normalized email address, not a resolved person. It has
header observations, display-name observations, dates, message counts, and
geographic evidence. The later authoritative-name index may merge Contacts
into People; it must not overwrite or collapse Contact evidence.

Contact extraction reads `From`, `To`, `Cc`, and `Bcc` once per message. Each
address contributes at most one appearance to a message's all-header count and
date range. The initial Contacts window displays the address, first and last
appearance, and number of messages. Its **Meaningful** filter is checked by
default. A contact is meaningful when it is a direct To or outgoing Bcc
recipient of an owner-sent message, or the From address of an incoming message
whose `To` header contains an exact configured owner address. Cc recipients
are not meaningful; multiple To recipients are. A mailing-list message is
meaningful only under that same direct-owner-in-To rule.

The existing `owner-names.txt` remains the current ingest classifier. The
Contacts implementation will ask for exact owner addresses when creating an
archive and allow them to be revised through **File → Properties**. Those
addresses supply the direct-owner test; name fragments must not do so.

`human-contacts` excludes non-human identities through a versioned local
policy: mailing-list providers/hosts, automated or no-reply services, malformed
or replacement-character addresses, and local parts longer than 48 characters.
Classification records a reason and is conservative: ordinary non-ASCII SMTPUTF8
addresses remain eligible. The policy is seeded from Google Groups' documented
group-email model and LISTSERV's CataList, but no maintained universal list-host
database exists; additions are narrow and reviewable.
Policy allow rules are explicit, documented exceptions evaluated before every
exclusion rule; they preserve known personal identities such as =@ex.com and
voicemail identities at vm.vonage.com.
The complete packaged policy may be copied to an archive root as
`contact_filters.yaml`. The archive-local policy is strictly validated
and takes precedence, preserving the exact filtering decision with the archive.
`mode: replace` requires a complete replacement policy; `mode: extend` unions
each supplied rule list with the packaged list in order and removes duplicates.
Extension does not change packaged scalar thresholds.

## Geographic evidence

Location evidence is append-only and provenance-bearing. A signature address,
city/state, ZCTA, or other contact-specific observation is **located**
evidence. A university domain or institution mapping is **affiliated**
evidence, never evidence of where an individual lives. The initial display
uses the latest credible location while preserving its source and observation
date. University mappings use only the institution's main campus.

The first United States search accepts a five-digit ZCTA such as `02139`, not a
three-digit ZIP prefix. The UI displays its city, state, and country beside the
input. A later place/radius search resolves a place centre to latitude and
longitude and applies straight-line distance; it is an estimate, not route
distance.

No per-contact public geocoding or domain-lookup service is used in this
phase. Geographic reference data is bulk-installed and refreshed locally. The
planned packaged application will include a seed US database with ZCTA, city, state,
country, and representative latitude/longitude for every available ZCTA. The
initial generator may derive it from the 2020 Census geography release. The
update pipeline may add university main-campus/domain data from downloaded
datasets; it does not infer location from arbitrary non-`.edu` domains.

## Storage and refresh

The installation-level reference database is shared by all archives:

| Platform | Directory |
|---|---|
| macOS | `~/Library/Application Support/Email Collection Toolkit/geography/` |
| Windows | `%LOCALAPPDATA%\\Email Collection Toolkit\\geography\\` |
| Linux | `$XDG_DATA_HOME/mailarchiver/geography/`, or `~/.local/share/mailarchiver/geography/` |

The planned `make geography-data` target will refresh this data. The future **Tools → Update Geo
Database** action invokes the same verified, atomic update path. Downloads
record source, release/version, retrieval date, and checksum before replacing a
local database.

An archive may optionally receive a read-only geography snapshot. The user may
also elect to load the user's geography from that snapshot rather than the
installation database. Copying is explicit: ordinary reference-data updates
never alter an archive.

## Database migration boundary

`archive.sqlite3`, `search.sqlite3`, and the installation geography database
have independent lifecycles. Archive schema changes use Python-managed SQL migrations with Flyway-style filenames and
an archive migration history and archive database version; migrations are applied in
place, transactionally where SQLite permits, with a backup/recovery plan.
Search remains disposable and is rebuilt rather than migrated. Geography
reference data has its own version and atomic replacement/update protocol. The
optional archive snapshot is migrated with the archive catalog, not with the
installation database.

The current V1 catalog does not yet implement this migration scheme. This
document is the contract for the Contacts/geography implementation work.
