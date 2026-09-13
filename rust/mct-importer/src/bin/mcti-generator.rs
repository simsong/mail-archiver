//! Synthetic MCT Importer API 1.0 producer; see doc/MCT_IMPORTER_API.md.
use mct_importer::{generate, API_VERSION};
use std::io;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() == 1 {
        match args[0].as_str() {
            "--help" | "-h" => {
                println!("Usage: mcti-generator COUNT\nEmit COUNT synthetic MCT Importer API {API_VERSION} mboxrd messages to stdout.");
                return ExitCode::SUCCESS;
            }
            "--api-version" => {
                println!("MCT Importer API {API_VERSION}");
                return ExitCode::SUCCESS;
            }
            "--version" => {
                println!("mcti-generator {}", env!("CARGO_PKG_VERSION"));
                return ExitCode::SUCCESS;
            }
            _ => {}
        }
        if !args[0].is_empty() && args[0].bytes().all(|b| b.is_ascii_digit()) {
            if let Ok(count) = args[0].parse::<u64>() {
                return match generate(count, &mut io::BufWriter::new(io::stdout().lock())) {
                    Ok(()) => ExitCode::SUCCESS,
                    Err(error) => {
                        eprintln!("mcti-generator: output failed: {error}");
                        ExitCode::FAILURE
                    }
                };
            }
        }
    }
    eprintln!("Usage: mcti-generator COUNT (unsigned integer; zero emits an empty stream)");
    ExitCode::from(2)
}
