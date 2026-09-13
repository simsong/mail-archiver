//! Substantive MCT Importer API 1.0 regressions; see doc/MCT_IMPORTER_API.md.
use mct_importer::{generate, validate, Report, DEFAULT_MAX_MESSAGE_BYTES};
use std::io::{Cursor, Write};
use std::process::{Command, Stdio};

fn stream(count: u64) -> Vec<u8> {
    let mut output = Vec::new();
    generate(count, &mut output).unwrap();
    output
}

fn check(data: &[u8]) -> (Report, Vec<String>) {
    let mut errors = Vec::new();
    let report = validate(&mut Cursor::new(data), DEFAULT_MAX_MESSAGE_BYTES, |e| {
        errors.push(e)
    })
    .unwrap();
    (report, errors)
}

#[test]
fn generator_has_real_rfc_headers_mime_counter_and_reversible_quotes() {
    let data = stream(3);
    assert_eq!(check(&data).0.complete, 3);
    let text = String::from_utf8(data).unwrap();
    assert!(text.contains("\r\n>From an unquoted source line\r\n>>From a literal source quotation\r\n>>>From a deeper source quotation\r\n"));
    for n in 1..=3 {
        assert!(text.contains(&format!("Message counter: {n}\r\n")));
        assert!(text.contains(&format!("Message-ID: <mcti-{n}@example.invalid>\r\n")));
    }
    // An independent MIME reader checks the first decoded message, not just our validator.
    let raw = text
        .split_once('\n')
        .unwrap()
        .1
        .split("\nFrom mcti-generator")
        .next()
        .unwrap();
    let decoded = raw.replace("\n>", "\n");
    let parsed = mailparse::parse_mail(decoded.as_bytes()).unwrap();
    assert_eq!(parsed.ctype.mimetype, "text/plain");
    assert!(parsed.subparts.is_empty());
    assert!(parsed
        .get_body()
        .unwrap()
        .starts_with("Message counter: 1\r\n"));
}

#[test]
fn empty_stream_and_final_record_are_counted_correctly() {
    assert!(stream(0).is_empty());
    assert_eq!(check(b"").0.complete, 0);
    assert_eq!(check(&stream(1)).0.complete, 1);
    let mut truncated = stream(2);
    truncated.truncate(truncated.len() - 2);
    let (report, errors) = check(&truncated);
    assert_eq!((report.complete, report.rejected), (1, 1));
    assert!(errors[0].contains("incomplete record"));
}

#[test]
fn malformed_fields_uris_and_encodings_fail_but_later_records_survive() {
    let original = String::from_utf8(stream(1)).unwrap();
    for (old, replacement) in [
        ("X-Imported-URI:", "X-Other:"),
        ("https://example.invalid/mcti-generator/1", "relative/path"),
        ("https://example.invalid/mcti-generator/1", "file:///bad%XY"),
        ("Date: Thu, 1 Jan 1970 00:00:00 +0000", "Date: impossible"),
        (
            "From: MCT Generator <generator@example.invalid>",
            "From: missing-address",
        ),
        ("Subject: MCT test message 1", "Subject missing colon"),
        ("MIME-Version: 1.0", "MIME-Version: 2.0"),
        (
            "Content-Transfer-Encoding: 7bit",
            "Content-Transfer-Encoding: base64",
        ),
        ("Content-Type: text/plain", "Content-Type: nonsense"),
    ] {
        let bad = original.replace(old, replacement) + &original;
        let (report, errors) = check(bad.as_bytes());
        assert_eq!(
            (report.complete, report.rejected),
            (1, 1),
            "{replacement}: {errors:?}"
        );
    }
}

#[test]
fn malformed_framing_header_folding_and_limits() {
    let original = String::from_utf8(stream(1)).unwrap();
    let folded = original.replace(
        "Subject: MCT test message 1",
        "Subject: MCT\r\n test message 1",
    );
    assert_eq!(check(folded.as_bytes()).0.complete, 1);
    let inherited = original.replace("Date:", "X-Importer-Name: original-source-value\r\nDate:");
    assert_eq!(check(inherited.as_bytes()).0.complete, 1);
    assert!(check(format!("banner\n{original}").as_bytes()).0.errors > 0);
    assert!(
        check(
            original
                .replace("From mcti-generator Thu", "From mcti-generator Bad")
                .as_bytes()
        )
        .0
        .errors
            > 0
    );
    let injected = original.replace(">From an unquoted source line", "From unescaped body text");
    assert!(check(injected.as_bytes()).0.errors > 0);
    let report = validate(&mut Cursor::new(original.as_bytes()), 10, |_| {}).unwrap();
    assert_eq!((report.complete, report.rejected), (0, 1));
    let long_line = original.replace("Message counter: 1", &"a".repeat(1024 * 1024 + 1));
    assert_eq!(check((long_line + &original).as_bytes()).0.complete, 1);
}

#[test]
fn multipart_requires_complete_nested_parts_and_closing_boundary() {
    let original = String::from_utf8(stream(1)).unwrap();
    let header = original.split("\r\n\r\n").next().unwrap().replace(
        "text/plain; charset=us-ascii",
        "multipart/mixed; boundary=outer",
    );
    let valid = format!("{header}\r\n\r\n--outer\r\nContent-Type: text/plain\r\n\r\nhello\r\n--outer\r\nContent-Type: application/octet-stream\r\nContent-Transfer-Encoding: base64\r\n\r\naGVsbG8=\r\n--outer--\r\n\n");
    assert_eq!(check(valid.as_bytes()).0.complete, 1);
    assert_eq!(
        check(valid.replace("--outer--\r\n", "").as_bytes())
            .0
            .complete,
        0
    );
    assert_eq!(
        check(valid.replace("aGVsbG8=", "%%%invalid%%%").as_bytes())
            .0
            .complete,
        0
    );
}

#[test]
fn actual_process_pipeline_and_cli_failures() {
    let producer = Command::new(env!("CARGO_BIN_EXE_mcti-generator"))
        .arg("1000")
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut producer = producer;
    let result = Command::new(env!("CARGO_BIN_EXE_mdti-validator"))
        .stdin(producer.stdout.take().unwrap())
        .output()
        .unwrap();
    assert!(producer.wait().unwrap().success());
    assert!(result.status.success());
    assert!(String::from_utf8_lossy(&result.stdout).contains("Complete messages received: 1000"));
    assert!(String::from_utf8_lossy(&result.stderr).contains("discards all input"));
    for arg in ["-1", "abc", "18446744073709551616"] {
        let invalid = Command::new(env!("CARGO_BIN_EXE_mcti-generator"))
            .arg(arg)
            .output()
            .unwrap();
        assert_eq!(invalid.status.code(), Some(2));
        assert!(invalid.stdout.is_empty());
    }
    let mut validator = Command::new(env!("CARGO_BIN_EXE_mdti-validator"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    validator
        .stdin
        .take()
        .unwrap()
        .write_all(b"broken input\n")
        .unwrap();
    let invalid = validator.wait_with_output().unwrap();
    assert_eq!(invalid.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&invalid.stdout).contains("Complete messages received: 0"));
}

#[test]
fn byte_encodings_and_header_boundaries_are_enforced() {
    let original = String::from_utf8(stream(1)).unwrap();
    for (old, new) in [
        ("Subject: MCT test message 1", "Subject: bad\x00value"),
        ("Message counter: 1", "binary\x00body"),
        ("Message counter: 1", "bare\rCR"),
        ("Message counter: 1", "nonascii é"),
        ("<mcti-1@example.invalid>", "<@>"),
        (
            "Date: Thu, 1 Jan 1970 00:00:00 +0000",
            "Date: 31 Feb 2024 00:00:00 +0000",
        ),
    ] {
        assert_eq!(
            check(original.replace(old, new).as_bytes()).0.complete,
            0,
            "{new}"
        );
    }
    let qp = original.replace(
        "Content-Transfer-Encoding: 7bit",
        "Content-Transfer-Encoding: quoted-printable",
    );
    assert_eq!(
        check(qp.replace("Message counter: 1", "counter=3A 1").as_bytes())
            .0
            .complete,
        1
    );
    assert_eq!(
        check(qp.replace("Message counter: 1", "bad=XX").as_bytes())
            .0
            .complete,
        0
    );
    let long_header = original.replace(
        "Subject: MCT test message 1",
        &format!("Subject: {}", "x".repeat(999)),
    );
    assert_eq!(check(long_header.as_bytes()).0.complete, 0);
    let lf = original.replace("\r\n", "\n");
    assert_eq!(check(lf.as_bytes()).0.complete, 1);
}

#[test]
fn nesting_limit_rejects_deep_input_without_unbounded_recursion() {
    let original = String::from_utf8(stream(1)).unwrap();
    let headers = original
        .split("\r\n\r\n")
        .next()
        .unwrap()
        .replace("text/plain; charset=us-ascii", "message/rfc822");
    let mut nested = "Subject: end\r\n\r\nbody\r\n".to_owned();
    for _ in 0..34 {
        nested = format!("Content-Type: message/rfc822\r\n\r\n{nested}");
    }
    let deep = format!("{headers}\r\n\r\n{nested}\n");
    let (report, errors) = check(deep.as_bytes());
    assert_eq!(report.complete, 0);
    assert!(errors[0].contains("nesting"));
}
