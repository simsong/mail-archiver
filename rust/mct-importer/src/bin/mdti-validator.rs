//! Discarding MCT Importer API 1.0 validator; see doc/MCT_IMPORTER_API.md.
use mct_importer::{validate, API_VERSION, DEFAULT_MAX_MESSAGE_BYTES};
use std::io;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let limit = match args.as_slice() {
        [] => DEFAULT_MAX_MESSAGE_BYTES,
        [flag] if flag == "--help" || flag == "-h" => {
            println!("Usage: mdti-validator [--max-message-bytes N]\nValidate MCT Importer API {API_VERSION} on stdin and discard all input.\nDefault maximum message size: {DEFAULT_MAX_MESSAGE_BYTES} bytes.");
            return ExitCode::SUCCESS;
        }
        [flag] if flag == "--api-version" => {
            println!("MCT Importer API {API_VERSION}");
            return ExitCode::SUCCESS;
        }
        [flag] if flag == "--version" => {
            println!("mdti-validator {}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        [flag, number]
            if flag == "--max-message-bytes"
                && !number.is_empty()
                && number.bytes().all(|b| b.is_ascii_digit()) =>
        {
            match number.parse::<usize>() {
                Ok(n) if n > 0 => n,
                _ => return usage(),
            }
        }
        _ => return usage(),
    };
    eprintln!("WARNING: mdti-validator discards all input; no messages will be saved.");
    match validate(&mut io::stdin().lock(), limit, |error| {
        eprintln!("validation error: {error}")
    }) {
        Ok(report) => {
            println!(
                "Complete messages received: {}\nRejected records: {}\nValidation errors: {}",
                report.complete, report.rejected, report.errors
            );
            if report.errors == 0 {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err(error) => {
            eprintln!("mdti-validator: input failed: {error}");
            ExitCode::from(2)
        }
    }
}

fn usage() -> ExitCode {
    eprintln!("Usage: mdti-validator [--max-message-bytes POSITIVE_INTEGER]");
    ExitCode::from(2)
}
