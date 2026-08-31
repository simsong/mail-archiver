# Date handling

The canonical archive stores a resolved UTC date for routing, reporting, and
search. The original RFC 5322 bytes and their original headers are never
rewritten.

## Resolution order

1. Parse the RFC `Date:` header and normalize it to UTC.
2. Parse timestamp suffixes from `Received:` headers. Drop the earliest and
   latest hop when at least three are valid, then use the UTC median. With one
   or two valid hops, use their untrimmed median. A `Date:` value more than two
   days from that median is replaced by the median and marked
   `received-median`.
3. If no usable `Received:` timestamp exists and the `Date:` value is missing
   or has an epoch-like year through 1980, inspect decoded text bodies. Parse
   embedded `Date:` header lines and standard quoted forms such as `On January
   1, 2003, at 07:21 AM, … wrote:`. Choose the most recent plausible candidate
   and mark the record `body-embedded`.
4. For an otherwise undated message, use typed source metadata, the previous
   resolved date in the same input stream, or a four-digit year in a
   filesystem source path, in that order.

Dates outside the configured plausible year range are retained as source bytes
but do not control routing. Date-resolution defects are recorded in the
catalog so a later review can distinguish a header date from derived evidence.

