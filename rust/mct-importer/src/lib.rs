//! MCT Importer API 1.0. Validation discards input; it never publishes an archive.
//! Requirements and limits: doc/MCT_IMPORTER_API.md.

use base64::{engine::general_purpose::STANDARD, Engine};
use mailparse::{MailAddr, MailHeader, MailHeaderMap};
use std::io::{self, BufRead, Write};
use uriparse::URI;

pub const API_VERSION: &str = "1.0";
pub const DEFAULT_MAX_MESSAGE_BYTES: usize = 64 * 1024 * 1024;
const MAX_LINE_BYTES: usize = 1024 * 1024;
const MAX_HEADER_BYTES: usize = 256 * 1024;
const IMPORT_HEADERS: [&str; 3] = ["X-Imported-URI", "X-Importer-Name", "X-Importer-Version"];
const CONTENT_TYPE: &str = "Content-Type";
const TRANSFER_ENCODING: &str = "Content-Transfer-Encoding";
const BOUNDARY: &str = "boundary";

#[derive(Default, Debug)]
pub struct Report {
    pub complete: u64,
    pub rejected: u64,
    pub errors: u64,
}

#[derive(Default)]
struct Record {
    envelope_valid: bool,
    oversized: bool,
    payload: Vec<u8>,
}

fn line_content(line: &[u8]) -> &[u8] {
    let content = line.strip_suffix(b"\n").unwrap_or(line);
    content.strip_suffix(b"\r").unwrap_or(content)
}

fn quoted_from(line: &[u8]) -> bool {
    let depth = line.iter().take_while(|b| **b == b'>').count();
    depth > 0 && line[depth..].starts_with(b"From ")
}

/// Generate deterministic RFC 2822 headers, one text/plain MIME part and a 1-based counter.
pub fn generate(count: u64, output: &mut impl Write) -> io::Result<()> {
    for counter in 1..=count {
        output.write_all(b"From mcti-generator Thu Jan  1 00:00:00 1970\n")?;
        let message = format!(
            "X-Imported-URI: https://example.invalid/mcti-generator/{counter}\r\n\
             X-Importer-Name: mcti-generator\r\n\
             X-Importer-Version: {}\r\n\
             Date: Thu, 1 Jan 1970 00:00:00 +0000\r\n\
             From: MCT Generator <generator@example.invalid>\r\n\
             To: Validator <validator@example.invalid>\r\n\
             Message-ID: <mcti-{counter}@example.invalid>\r\n\
             Subject: MCT test message {counter}\r\n\
             MIME-Version: 1.0\r\n\
             Content-Type: text/plain; charset=us-ascii\r\n\
             Content-Transfer-Encoding: 7bit\r\n\r\n\
             Message counter: {counter}\r\n\
             From an unquoted source line\r\n\
             >From a literal source quotation\r\n\
             >>From a deeper source quotation\r\n",
            env!("CARGO_PKG_VERSION")
        );
        for line in message.as_bytes().split_inclusive(|b| *b == b'\n') {
            if line.starts_with(b"From ") || quoted_from(line) {
                output.write_all(b">")?;
            }
            output.write_all(line)?;
        }
        output.write_all(b"\n")?; // A separate, mandatory MBOX record-terminating blank line.
    }
    output.flush()
}

/// Read even invalid lines to completion, bounding allocation independently of their length.
fn bounded_line(input: &mut impl BufRead, line: &mut Vec<u8>) -> io::Result<bool> {
    line.clear();
    let mut overflow = false;
    loop {
        let available = input.fill_buf()?;
        if available.is_empty() {
            return Ok(overflow);
        }
        let length = available
            .iter()
            .position(|b| *b == b'\n')
            .map_or(available.len(), |i| i + 1);
        let ended = available[length - 1] == b'\n';
        let keep = length.min(MAX_LINE_BYTES.saturating_sub(line.len()));
        line.extend_from_slice(&available[..keep]);
        overflow |= keep < length;
        input.consume(length);
        if ended {
            return Ok(overflow);
        }
    }
}

fn envelope_valid(line: &[u8]) -> bool {
    let Ok(value) = std::str::from_utf8(line_content(line)) else {
        return false;
    };
    let fields: Vec<_> = value.split_ascii_whitespace().collect();
    fields.len() == 7
        && fields[0] == "From"
        && !fields[1].is_empty()
        && ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].contains(&fields[2])
        && chrono::DateTime::parse_from_rfc2822(&format!(
            "{}, {} {} {} {} +0000",
            fields[2], fields[4], fields[3], fields[6], fields[5]
        ))
        .is_ok()
        && line.ends_with(b"\n")
}

fn finish(record: Record, ordinal: u64, report: &mut Report, error: &mut impl FnMut(String)) {
    let result = if !record.envelope_valid {
        Err("invalid From separator".to_owned())
    } else if record.oversized {
        Err("message or physical line exceeds validation size limit".to_owned())
    } else {
        let payload = &record.payload;
        let stripped = payload
            .strip_suffix(b"\r\n")
            .or_else(|| payload.strip_suffix(b"\n"));
        match stripped.filter(|rest| rest.ends_with(b"\n")) {
            Some(raw) => validate_entity(raw, true, 0),
            None => Err("incomplete record: missing terminating blank line".to_owned()),
        }
    };
    match result {
        Ok(()) => report.complete += 1,
        Err(reason) => {
            report.rejected += 1;
            report.errors += 1;
            error(format!("record {ordinal}: {reason}"));
        }
    }
}

/// Stream records, decode one mboxrd quoting level, report the first error per rejected record.
/// A valid EOF record is syntactically complete; upstream exit status is not observable here.
pub fn validate(
    input: &mut impl BufRead,
    max_bytes: usize,
    mut error: impl FnMut(String),
) -> io::Result<Report> {
    let mut report = Report::default();
    let mut record: Option<Record> = None;
    let mut ordinal = 0;
    let mut line = Vec::new();
    loop {
        let overflow = bounded_line(input, &mut line)?;
        if line.is_empty() {
            break;
        }
        if line.starts_with(b"From ") {
            if let Some(previous) = record.take() {
                finish(previous, ordinal, &mut report, &mut error);
            }
            ordinal += 1;
            record = Some(Record {
                envelope_valid: !overflow && envelope_valid(&line),
                ..Record::default()
            });
        } else if let Some(current) = record.as_mut() {
            let decoded = if quoted_from(&line) {
                &line[1..]
            } else {
                &line
            };
            current.oversized |=
                overflow || decoded.len() > max_bytes.saturating_sub(current.payload.len());
            if !current.oversized {
                current.payload.extend_from_slice(decoded);
            }
        } else {
            report.errors += 1;
            error("data before the first From separator".to_owned());
        }
    }
    if let Some(last) = record {
        finish(last, ordinal, &mut report, &mut error);
    }
    Ok(report)
}

fn one(headers: &[MailHeader<'_>], name: &str) -> Result<String, String> {
    let values = headers.get_all_values(name);
    if values.len() != 1 || values[0].trim().is_empty() {
        return Err(format!("expected exactly one nonempty {name} header"));
    }
    Ok(values[0].clone())
}

fn validate_headers(raw: &[u8]) -> Result<(), String> {
    let mut bytes = 0;
    let mut have_header = false;
    for line in raw.split_inclusive(|b| *b == b'\n') {
        let content = line_content(line);
        bytes += line.len();
        if content.is_empty() {
            return Ok(());
        }
        if bytes > MAX_HEADER_BYTES || content.len() > 998 {
            return Err("header size limit exceeded".into());
        }
        if !line.ends_with(b"\n")
            || content
                .iter()
                .any(|b| !b.is_ascii() || (*b < 32 && *b != b'\t') || *b == 127)
        {
            return Err("invalid header byte or line ending".into());
        }
        if content.starts_with(b" ") || content.starts_with(b"\t") {
            if !have_header {
                return Err("header continuation without a field".into());
            }
        } else {
            let Some(colon) = content.iter().position(|b| *b == b':') else {
                return Err("header without colon".into());
            };
            if colon == 0 || content[..colon].iter().any(|b| !(33..=126).contains(b)) {
                return Err("invalid header field name".into());
            }
            have_header = true;
        }
    }
    Err("missing header/body separator".into())
}

fn validate_origin(headers: &[MailHeader<'_>]) -> Result<(), String> {
    chrono::DateTime::parse_from_rfc2822(&one(headers, "Date")?)
        .map_err(|_| "invalid Date".to_owned())?;
    one(headers, "From")?;
    let from = headers.get_first_header("From").ok_or("missing From")?;
    let addresses = mailparse::addrparse_header(from).map_err(|_| "invalid From address list")?;
    if addresses.is_empty()
        || addresses
            .iter()
            .any(|a| !matches!(a, MailAddr::Single(s) if s.addr.contains('@')))
    {
        return Err("From must contain mailboxes".into());
    }
    if addresses.len() > 1 || headers.get_first_header("Sender").is_some() {
        one(headers, "Sender")?;
        let sender = headers.get_first_header("Sender").ok_or("missing Sender")?;
        let parsed = mailparse::addrparse_header(sender).map_err(|_| "invalid Sender")?;
        if parsed.len() != 1 || !matches!(&parsed[0], MailAddr::Single(s) if s.addr.contains('@')) {
            return Err("Sender must contain one mailbox".into());
        }
    }
    if headers.get_first_header("Message-ID").is_some() {
        let id = one(headers, "Message-ID")?;
        let inner = id.strip_prefix('<').and_then(|s| s.strip_suffix('>'));
        if !inner.is_some_and(|s| {
            s.split_once('@')
                .is_some_and(|(local, domain)| !local.is_empty() && !domain.is_empty())
                && s.bytes().filter(|b| *b == b'@').count() == 1
                && !s
                    .bytes()
                    .any(|b| b.is_ascii_whitespace() || b"<>".contains(&b))
        }) {
            return Err("invalid Message-ID".into());
        }
    }
    Ok(())
}

fn validate_entity(raw: &[u8], top: bool, depth: usize) -> Result<(), String> {
    if depth > 32 {
        return Err("MIME nesting exceeds 32 levels".into());
    }
    validate_headers(raw)?;
    let (headers, body_start) =
        mailparse::parse_headers(raw).map_err(|e| format!("invalid headers: {e}"))?;
    let body = &raw[body_start..];
    if top {
        for (i, name) in IMPORT_HEADERS.iter().enumerate() {
            let field = headers.get(i).ok_or_else(|| format!("missing {name}"))?;
            if !field.get_key().eq_ignore_ascii_case(name) || field.get_value().trim().is_empty() {
                return Err(format!(
                    "generated header {} must be {name} with a value",
                    i + 1
                ));
            }
            // Generated provenance uses literal ASCII, not RFC 2047 encoded words.
            let value = std::str::from_utf8(field.get_value_raw())
                .map_err(|_| "non-ASCII importer header")?
                .trim();
            if value.contains(['\r', '\n']) || !value.is_ascii() || value.starts_with("=?") {
                return Err("generated importer fields must be single-line ASCII".into());
            }
            if i == 0 && URI::try_from(value).is_err() {
                return Err("X-Imported-URI must be an absolute escaped URI".into());
            }
        }
        validate_origin(&headers)?;
        if one(&headers, "MIME-Version")? != "1.0" {
            return Err("MIME-Version must be 1.0".into());
        }
    }
    let type_value = if top || headers.get_first_header(CONTENT_TYPE).is_some() {
        one(&headers, CONTENT_TYPE)?
    } else {
        "text/plain".into()
    };
    let media = type_value.split(';').next().unwrap_or("").trim();
    let token = |v: &str| {
        !v.is_empty()
            && v.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&b))
    };
    if !media
        .split_once('/')
        .is_some_and(|(a, b)| token(a) && token(b))
    {
        return Err("invalid Content-Type".into());
    }
    let content_type = mailparse::parse_content_type(&type_value);
    let encoding = if headers.get_first_header(TRANSFER_ENCODING).is_some() {
        one(&headers, TRANSFER_ENCODING)?.to_ascii_lowercase()
    } else {
        "7bit".into()
    };
    if ["7bit", "8bit"].contains(&encoding.as_str())
        && (body.contains(&0)
            || (encoding == "7bit" && !body.is_ascii())
            || body
                .iter()
                .enumerate()
                .any(|(i, b)| *b == b'\r' && body.get(i + 1) != Some(&b'\n'))
            || body
                .split(|b| *b == b'\n')
                .any(|l| l.strip_suffix(b"\r").unwrap_or(l).len() > 998))
    {
        return Err("body violates declared transfer encoding".into());
    }
    if content_type.mimetype.starts_with("multipart/") {
        if !["7bit", "8bit", "binary"].contains(&encoding.as_str()) {
            return Err("encoded multipart container".into());
        }
        let boundary = content_type
            .params
            .get(BOUNDARY)
            .ok_or("multipart lacks boundary")?;
        if boundary.is_empty()
            || boundary.len() > 70
            || boundary.ends_with(' ')
            || !boundary
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"'()+_,-./:=? ".contains(&b))
        {
            return Err("invalid MIME boundary".into());
        }
        let marker = format!("--{boundary}");
        let close = format!("{marker}--");
        let mut start = None;
        let mut offset = 0;
        let mut parts = 0;
        for line in body.split_inclusive(|b| *b == b'\n') {
            let value = line_content(line).trim_ascii_end();
            if value == marker.as_bytes() || value == close.as_bytes() {
                if let Some(begin) = start {
                    validate_entity(&body[begin..offset], false, depth + 1)?;
                    parts += 1;
                }
                if value == close.as_bytes() {
                    return if parts > 0 {
                        Ok(())
                    } else {
                        Err("multipart has no parts".into())
                    };
                }
                start = Some(offset + line.len());
            }
            offset += line.len();
        }
        return Err("multipart lacks closing boundary".into());
    }
    let decoded = match encoding.as_str() {
        "base64" => {
            if body
                .split(|b| *b == b'\n')
                .any(|l| l.strip_suffix(b"\r").unwrap_or(l).len() > 76)
            {
                return Err("base64 line exceeds 76 bytes".into());
            }
            let encoded: Vec<_> = body
                .iter()
                .copied()
                .filter(|b| !b" \t\r\n".contains(b))
                .collect();
            STANDARD
                .decode(encoded)
                .map_err(|_| "invalid base64 body")?
        }
        "quoted-printable" => quoted_printable::decode(body, quoted_printable::ParseMode::Strict)
            .map_err(|_| "invalid quoted-printable body")?,
        "7bit" | "8bit" | "binary" => {
            if content_type.mimetype == "message/rfc822" {
                return validate_entity(body, false, depth + 1);
            }
            return Ok(());
        }
        _ => return Err("unsupported Content-Transfer-Encoding".into()),
    };
    if content_type.mimetype == "message/rfc822" {
        validate_entity(&decoded, false, depth + 1)?;
    }
    Ok(())
}
