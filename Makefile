# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

.PHONY: auth-detect-live benchmark-name-resolution check compare-apple-mail data-quality-audit data-quality-babyl-audit data-quality-summary extract-pdf-mail fixture-bagit fixture-e2e gui gui-smoke website-build-check website-check release-tag-check
.PHONY: install-linux install-mac install-test-browser install-tika ocr-analyze ocr-experiment ocr-inventory ocr-profile ocr-run pylint run search summary-smoke test test-bagit test-data-quality
.PHONY: test-application test-e2e test-encoding test-gui test-headers test-mailsearch test-native-gui test-native-html-find test-pdf-mail test-plugins test-progress test-provenance test-refresh-index test-tika test-website validation-aws-start validation-aws-start-all

.PHONY: test-apple-mail-compare test-auth
.PHONY: validation-fetch validation-list validation-prepare validation-run validation-run-all validation-sam-build validation-sam-deploy validation-sam-validate validation-test verify

.PHONY: addressbook-export test-addressbook-export

addressbook-export:
	uv run --locked python dev/addressbook-exporter.py $(ARGS)

test-addressbook-export: ruff
	uv run --locked pylint dev/addressbook-exporter.py tests/test_addressbook_exporter.py
	uv run --locked ty check dev/addressbook-exporter.py tests/test_addressbook_exporter.py --error-on-warning
	uv run --locked pyright dev/addressbook-exporter.py tests/test_addressbook_exporter.py --warnings
	uv run --locked pytest -q tests/test_addressbook_exporter.py

.PHONY: test-copyright build-sdist copyright-check runtime-license-check runtime-license-bundle
DIST_DIR ?= $(CURDIR)/dist

build-sdist: ruff copyright-check runtime-license-check
	uv build --sdist --out-dir "$(DIST_DIR)"

test-copyright:
	uv run --locked pytest -q tests/test_copyright.py

# Missing copyright notices warn without failing CI or release builds.
copyright-check:
	uv run python scripts/check_copyright.py

runtime-license-check:
	uv run python scripts/check_runtime_licenses.py

runtime-license-bundle:
	@test -n "$(LICENSE_OUTPUT)" || { echo 'usage: make runtime-license-bundle LICENSE_OUTPUT=/path/to/licenses'; exit 2; }
	uv run python scripts/check_runtime_licenses.py --output "$(LICENSE_OUTPUT)"

TIKA_VERSION ?= 4.0.0
.PHONY: sync-dependencies test-reconciliation distribution-check name-matcher-observations h3-ambiguous-review

sync-dependencies:
	uv sync

test-reconciliation:
	uv run pytest -q tests/test_contacts.py tests/test_contact_filtering.py tests/test_name_matcher_research.py tests/test_apple_mail_compare.py tests/test_scanner.py

distribution-check:
	uv run python scripts/check_distribution.py

name-matcher-observations:
	@test -n "$(ARCHIVE)" -a -n "$(OUTPUT)" || { echo 'usage: make name-matcher-observations ARCHIVE=/path/to/archive OUTPUT=/path/to/evidence.sqlite3'; exit 2; }
	uv run python -m scripts.name_matcher.build_observations --archive "$(ARCHIVE)" --output "$(OUTPUT)" $(ARGS)

h3-ambiguous-review:
	uv run mailarchiver-h3-review $(ARGS)

TIKA_DIR ?= $(CURDIR)/.tools/tika/$(TIKA_VERSION)
TIKA_JAR := $(TIKA_DIR)/tika-app-$(TIKA_VERSION).jar
TIKA_DOWNLOAD_DIR ?= $(CURDIR)/.tools/tika/downloads
TIKA_ARCHIVE := $(TIKA_DOWNLOAD_DIR)/tika-app-$(TIKA_VERSION).zip
TIKA_SHA512 := $(TIKA_ARCHIVE).sha512
TIKA_URL := https://downloads.apache.org/tika/$(TIKA_VERSION)/tika-app-$(TIKA_VERSION).zip
PLAYWRIGHT_INSTALL_ARGS ?= chromium
NATIVE_GUI_ARTIFACT_DIR ?= $(CURDIR)/.tmp/native-gui-diagnostics
AUDIT_OUTPUT ?= $(CURDIR)/.tmp/data-quality-audit
VALIDATION_DATA_DIR ?= $(CURDIR)/data
VALIDATION_CONFIG_DIR ?= $(CURDIR)/validation/datasets
VALIDATION_SAM_CONFIG ?= validation/samconfig.toml
OCR_OUTPUT ?= $(CURDIR)/ocr-text
OCR_WORKERS ?= 4
OCR_ENGINES ?= native,ocrmypdf,tesseract
OCR_INVENTORY_ARGS ?=
OCR_RUN_ARGS ?=

.PHONY: lint ruff types ty pyright
# Recursive recipes preserve stage ordering even with make -j.
check:
	$(MAKE) lint
	$(MAKE) types
	$(MAKE) copyright-check
	$(MAKE) runtime-license-check
	$(MAKE) test
	$(MAKE) test-e2e
	$(MAKE) website-check

lint:
	$(MAKE) ruff
	$(MAKE) pylint

ruff:
	git ls-files -z --cached --others --exclude-standard -- '*.py' '*.pyi' | \
		xargs -0 uv run --locked ruff check --config pyproject.toml

types:
	$(MAKE) ty
	$(MAKE) pyright

ty:
	uv run --locked ty check --error-on-warning

pyright:
	uv run --locked pyright --warnings

.PHONY: syntax-check
syntax-check:
	uv run python -m compileall -q src scripts tests e2e_tests

.PHONY: dmg test-dmg preview-dmg self-test self-test-gui test-packaging
dmg: ruff syntax-check
	uv run --group packaging python scripts/build_macos.py $(ARGS)

test-dmg:
	@test -n "$(DMG)" || { echo 'usage: make test-dmg DMG=/path/to/Email-Collection-Toolkit.dmg'; exit 2; }
	uv run --group packaging python scripts/build_macos.py --test-dmg "$(DMG)"

preview-dmg: ruff
	@test -n "$(DMG)" || { echo 'usage: make preview-dmg DMG=/path/to/Email-Collection-Toolkit.dmg'; exit 2; }
	uv run --group packaging python scripts/build_macos.py --preview-dmg "$(DMG)"

self-test:
	uv run python scripts/desktop_entry.py --self-test $(ARGS)

self-test-gui:
	uv run mailsearch-gui --self-test-gui $(ARGS)

test-packaging:
	uv run pytest -q tests/test_packaging.py tests/test_macos_signing.py

.PHONY: test-signing
test-signing: ruff
	PYTHONPATH="$(CURDIR)" uv run --locked pylint scripts/macos_signing.py scripts/build_macos.py tests/test_macos_signing.py
	uv run --locked ty check scripts/macos_signing.py scripts/build_macos.py tests/test_macos_signing.py --error-on-warning
	uv run --locked pyright scripts/macos_signing.py scripts/build_macos.py tests/test_macos_signing.py --warnings
	uv run --locked pytest -q tests/test_macos_signing.py tests/test_website_scripts.py

auth-detect-live:
	uv run mailarchiver-auth --detect-only simsong@gmail.com
	uv run mailarchiver-auth --detect-only simsong@basistech.com
	uv run mailarchiver-auth --detect-only sgarfinkel@fas.harvard.edu

compare-apple-mail:
	uv run mailarchiver-compare-apple-mail --apple-mail "$(HOME)/Library/Mail" --archive "$(HOME)/mail-archive" $(ARGS)

data-quality-audit:
	@test -n "$(ARCHIVE)" || { echo 'usage: make data-quality-audit ARCHIVE=/path/to/mailbag EARLY_SOURCE=/path/to/source'; exit 2; }
	@test -n "$(EARLY_SOURCE)" || { echo 'usage: make data-quality-audit ARCHIVE=/path/to/mailbag EARLY_SOURCE=/path/to/source'; exit 2; }
	@echo "Writing private data-quality evidence under $(AUDIT_OUTPUT)"
	@mkdir -p "$(AUDIT_OUTPUT)"
	uv run python scripts/data_quality/analyze_archive.py --archive "$(ARCHIVE)" --early-source "$(EARLY_SOURCE)" --output "$(AUDIT_OUTPUT)"

data-quality-babyl-audit:
	@test -n "$(EARLY_SOURCE)" || { echo 'usage: make data-quality-babyl-audit EARLY_SOURCE=/path/to/source'; exit 2; }
	@echo "Writing private Babyl evidence under $(AUDIT_OUTPUT)"
	@mkdir -p "$(AUDIT_OUTPUT)"
	uv run python scripts/data_quality/audit_babyl.py --source "$(EARLY_SOURCE)" --output "$(AUDIT_OUTPUT)"

benchmark-name-resolution:
	uv run python scripts/benchmark_name_resolution.py

data-quality-summary:
	@test -d "$(AUDIT_OUTPUT)" || { echo "missing audit output directory: $(AUDIT_OUTPUT)"; exit 2; }
	uv run python scripts/data_quality/summarize_evidence.py "$(AUDIT_OUTPUT)"

fixture-bagit:
	uv run python tests/generate_bagit_fixture.py tests/data/three-message-mailbag

fixture-e2e:
	uv run python e2e_tests/generate_corpus.py e2e_tests/data/source

extract-pdf-mail:
	uv run extract-pdf-mail $(ARGS)

pylint:
	uv run --locked pylint src tests e2e_tests scripts

run:
	uv run mailarchiver $(ARGS)

search:
	uv run mailsearch $(ARGS)

verify:
	@test -n "$(ARCHIVE)" || { echo 'usage: make verify ARCHIVE=/path/to/mailbag'; exit 2; }
	uv run verify-mail-archive "$(ARCHIVE)"

summary-smoke:
	@printf '%s\n' 'During verification, the archiver reads every canonical MBOX file without changing it. It checks BagIt payload and tag manifests, Mailbag metadata, and every declared complete-MBOX, raw-message, and semantic-message digest. Derived SQLite search data can be regenerated from canonical MBOX files. A failure is reported for investigation rather than repaired automatically.' | uv run summarize

gui:
	uv run mailsearch-gui $(ARGS)

gui-smoke: test-native-gui

.PHONY: test-native-application
test-native-application:
	MAILARCHIVER_NATIVE_APPLICATION_E2E=1 uv run pytest -q e2e_tests/test_ingest_verify.py::test_native_application_lifecycle

.PHONY: check-archive-open
check-archive-open:
	@test -n "$(ARCHIVE)" || { echo 'usage: make check-archive-open ARCHIVE=/path/to/archive'; exit 2; }
	uv run python -c 'import sys; from pathlib import Path; from mailarchiver.application import validate_archive; print(validate_archive(Path(sys.argv[1]))[0])' "$(ARCHIVE)"

website-check:
	uv run python scripts/check_website.py

.PHONY: website-preview
WEBSITE_PREVIEW_PORT ?= 1111
website-preview:
	zola --root website serve --interface 127.0.0.1 --port $(WEBSITE_PREVIEW_PORT) --output-dir "$(CURDIR)/.tmp/website-preview" --force

website-build-check: website-check
	zola --root website build --output-dir "$(CURDIR)/.tmp/website-check" --force

release-tag-check:
	@test -n "$(GITHUB_REF_NAME)" || { echo 'usage: make release-tag-check GITHUB_REF_NAME=v1.2.3'; exit 2; }
	uv run --no-project --python '>=3.12' python scripts/release_tag.py --tag "$(GITHUB_REF_NAME)" $(ARGS)

test:
	uv run pytest -q

.PHONY: test-corpus-import update-corpus-expectations
test-corpus-import:
	uv run pytest -q tests/test_corpus_import.py

update-corpus-expectations:
	uv run pytest -q -s tests/test_corpus_import.py --update-corpus-expectations

test-application:
	uv run pytest -q tests/test_application.py tests/test_writer_lock.py tests/test_loopback.py

test-apple-mail-compare:
	uv run pytest -q tests/test_apple_mail_compare.py

test-auth:
	uv run pytest -q tests/test_auth.py

test-e2e:
	uv run pytest -q --browser chromium --tracing=retain-on-failure e2e_tests

test-encoding:
	uv run pytest -q tests/test_encoding.py

test-native-gui:
	MAILARCHIVER_NATIVE_GUI_ARTIFACT_DIR="$(NATIVE_GUI_ARTIFACT_DIR)" MAILARCHIVER_NATIVE_GUI_E2E=1 uv run pytest -q e2e_tests/test_ingest_verify.py::test_native_search_ui_smoke

test-native-html-find:
	@test "$$(uname)" = Darwin || { echo 'test-native-html-find requires macOS'; exit 1; }
	MAILARCHIVER_NATIVE_GUI_ARTIFACT_DIR="$(NATIVE_GUI_ARTIFACT_DIR)" MAILARCHIVER_NATIVE_HTML_FIND_E2E=1 uv run pytest -q e2e_tests/test_ingest_verify.py::test_native_html_find_smoke

test-pdf-mail:
	@command -v pdftotext >/dev/null || { echo 'test-pdf-mail requires Poppler pdftotext'; exit 1; }
	uv run pytest -q tests/test_pdf_mail.py

test-bagit:
	uv run pytest -q tests/test_bagit.py tests/test_standalone_verify.py

test-data-quality:
	uv run pytest -q tests/test_data_quality_scripts.py

test-mailsearch:
	uv run pytest -q tests/test_mailsearch.py

test-name-resolution:
	uv run pytest -q tests/test_name_resolution_benchmark.py tests/test_name_matcher_research.py

test-gui:
	uv run pytest -q tests/test_gui_service.py

test-provenance:
	uv run pytest -q tests/test_catalog.py tests/test_sources.py

test-refresh-index:
	uv run pytest -q tests/test_end_to_end.py::test_refresh_index_excludes_quarantine_mailboxes tests/test_end_to_end.py::test_refresh_index_interrupt_retains_prior_search_index

test-headers:
	uv run pytest -q tests/test_message.py tests/test_search.py

test-progress:
	uv run pytest -q tests/test_progress.py tests/test_sources.py

test-tika:
	uv run pytest -q tests/test_tika.py

test-website:
	uv run pytest -q tests/test_website_scripts.py

.PHONY: test-website-navigation
test-website-navigation:
	uv run pytest -q --browser chromium e2e_tests/test_website_navigation.py

test-plugins:
	uv run pytest -q tests/test_plugin_loader.py tests/test_source_integrity.py tests/test_archive_integrity.py

validation-list:
	@echo "Listing validation datasets configured in $(VALIDATION_CONFIG_DIR)"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" list

validation-fetch:
	@test -n "$(DATASET)" || { echo 'usage: make validation-fetch DATASET=dataset-id'; exit 2; }
	@echo "Downloading and fixity-recording validation dataset $(DATASET) under $(VALIDATION_DATA_DIR)"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" fetch "$(DATASET)"

validation-prepare:
	@test -n "$(DATASET)" || { echo 'usage: make validation-prepare DATASET=dataset-id'; exit 2; }
	@echo "Safely extracting and preprocessing validation dataset $(DATASET) under $(VALIDATION_DATA_DIR)"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" prepare "$(DATASET)"

validation-run:
	@test -n "$(DATASET)" || { echo 'usage: make validation-run DATASET=dataset-id'; exit 2; }
	@echo "Acquiring, preprocessing, ingesting, independently verifying, and packaging $(DATASET)"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" run "$(DATASET)"

validation-run-all:
	@echo "Running every enabled public validation dataset locally and sequentially"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" run-all

validation-test:
	uv run pytest -q tests/test_validation.py tests/test_validation_launcher.py

validation-sam-build:
	@echo "Building the validation EC2 launcher SAM application"
	sam build --template-file validation/template.yaml

validation-sam-validate:
	@echo "Validating the validation EC2 launcher SAM template"
	sam validate --lint --template-file validation/template.yaml

validation-sam-deploy: validation-sam-build
	@test -f "$(VALIDATION_SAM_CONFIG)" || { echo "Create ignored $(VALIDATION_SAM_CONFIG) from validation/samconfig.example.toml"; exit 2; }
	@echo "Deploying the long-lived validation control plane; the configured output bucket remains external"
	sam deploy --config-file "$(VALIDATION_SAM_CONFIG)"

validation-aws-start:
	@test -n "$(DATASET)" || { echo 'usage: make validation-aws-start DATASET=dataset-id STACK=stack-name'; exit 2; }
	@test -n "$(STACK)" || { echo 'usage: make validation-aws-start DATASET=dataset-id STACK=stack-name'; exit 2; }
	@echo "Launching one self-terminating EC2 worker for validation dataset $(DATASET)"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" aws-start "$(DATASET)" --stack "$(STACK)"

validation-aws-start-all:
	@test -n "$(STACK)" || { echo 'usage: make validation-aws-start-all STACK=stack-name'; exit 2; }
	@echo "Launching one independent self-terminating EC2 worker for every enabled validation dataset"
	uv run mailarchiver-validation --config-dir "$(VALIDATION_CONFIG_DIR)" --data-dir "$(VALIDATION_DATA_DIR)" aws-start-all --stack "$(STACK)"

install-mac:
	@test "$$(uname -s)" = Darwin || { echo "install-mac must run on macOS"; exit 1; }
	@$(MAKE) install-tika

install-linux:
	@test "$$(uname -s)" = Linux || { echo "install-linux must run on Linux"; exit 1; }
	@$(MAKE) install-tika

install-test-browser:
	uv run playwright install $(PLAYWRIGHT_INSTALL_ARGS)

install-tika:
	@command -v java >/dev/null || { echo "Apache Tika needs a Java runtime (Java 17 or newer)."; exit 1; }
	@test ! -e "$(TIKA_DIR)" || { echo "Tika is already installed at $(TIKA_DIR)"; exit 1; }
	@mkdir -p "$(TIKA_DOWNLOAD_DIR)"
	curl --fail --location --output "$(TIKA_ARCHIVE)" "$(TIKA_URL)"
	curl --fail --location --output "$(TIKA_SHA512)" "$(TIKA_URL).sha512"
	uv run python -m mailarchiver.tika --archive "$(TIKA_ARCHIVE)" --checksum "$(TIKA_SHA512)" --destination "$(TIKA_DIR)" --version "$(TIKA_VERSION)"
	@echo "Installed and verified $(TIKA_JAR)"

ocr-inventory:
	@test -n "$(ARCHIVE)" || { echo 'usage: make ocr-inventory ARCHIVE=/path/to/mailbag'; exit 2; }
	@echo "Reading PDF attachments without modifying $(ARCHIVE)"
	@echo "Writing private, resumable experiment data under $(OCR_OUTPUT)"
	uv run python scripts/ocr_experiment.py inventory --archive "$(ARCHIVE)" --output "$(OCR_OUTPUT)" $(OCR_INVENTORY_ARGS)

ocr-profile:
	@test -f "$(OCR_OUTPUT)/documents.jsonl" || { echo "missing OCR inventory: $(OCR_OUTPUT)/documents.jsonl"; exit 2; }
	@echo "Profiling every unique PDF without changing it"
	uv run python scripts/ocr_experiment.py profile --output "$(OCR_OUTPUT)" --workers "$(OCR_WORKERS)"

ocr-analyze:
	@test -f "$(OCR_OUTPUT)/documents.jsonl" || { echo "missing OCR inventory: $(OCR_OUTPUT)/documents.jsonl"; exit 2; }
	@echo "Scanning every available text result for OCR artifacts and email-like structure"
	uv run python scripts/ocr_experiment.py analyze --output "$(OCR_OUTPUT)" --engines "$(OCR_ENGINES)"

ocr-run:
	@test -f "$(OCR_OUTPUT)/documents.jsonl" || { echo "missing OCR inventory: $(OCR_OUTPUT)/documents.jsonl"; exit 2; }
	@echo "Running OCR engines: $(OCR_ENGINES)"
	@echo "Each successful engine result is a separate text file; source PDFs are never rewritten."
	uv run python scripts/ocr_experiment.py run --output "$(OCR_OUTPUT)" --engines "$(OCR_ENGINES)" --workers "$(OCR_WORKERS)" $(OCR_RUN_ARGS)

ocr-experiment: ocr-inventory ocr-run

.PHONY: test-writer-lock
test-writer-lock:
	uv run pytest -q tests/test_writer_lock.py

.PHONY: website-icons
website-icons:
	uv run python -m scripts.render_website_icons

.PHONY: website-screenshots
website-screenshots:
	uv run --group dev python -m scripts.website_screenshots

.PHONY: website-gmail-illustrations
website-gmail-illustrations:
	uv run --group dev python -m scripts.gmail_setup_illustrations

.PHONY: test-envelopes
test-envelopes:
	uv run pytest -q tests/test_envelopes.py tests/test_sources.py tests/test_publication.py tests/test_standalone_verify.py tests/test_ingest_diagnostics.py tests/test_mbox_framing.py \
		tests/test_end_to_end.py::test_parser_failure_records_source_identity_and_failed_run

.PHONY: test-startup test-native-setup
test-startup:
	uv run pytest -q tests/test_application.py e2e_tests/test_startup.py --browser chromium

test-native-setup:
	MAILARCHIVER_NATIVE_SETUP_E2E=1 uv run pytest -q e2e_tests/test_startup.py -k native

.PHONY: test-file-drag
test-file-drag:
	uv run --locked pytest -q tests/test_file_drag.py

.PHONY: test-owner-rules
test-owner-rules: ruff
	uv run pytest -q tests/test_owner_rules.py tests/test_gui_service.py tests/test_application.py
