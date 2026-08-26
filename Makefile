.PHONY: check fixture-bagit fixture-e2e gui gui-smoke install-linux install-mac install-test-browser install-tika pylint run search summary-smoke test test-bagit test-e2e test-gui test-headers test-mailsearch test-native-gui test-progress test-provenance verify

TIKA_VERSION ?= 3.3.2
TIKA_DIR ?= $(CURDIR)/.tools/tika/$(TIKA_VERSION)
TIKA_JAR := $(TIKA_DIR)/tika-app-$(TIKA_VERSION).jar
TIKA_SHA512 := $(TIKA_JAR).sha512
TIKA_URL := https://downloads.apache.org/tika/$(TIKA_VERSION)/tika-app-$(TIKA_VERSION).jar
PLAYWRIGHT_INSTALL_ARGS ?= chromium

check: test test-e2e

fixture-bagit:
	uv run python tests/generate_bagit_fixture.py tests/data/three-message-mailbag

fixture-e2e:
	uv run python e2e_tests/generate_corpus.py e2e_tests/data/source

pylint:
	uv run pylint src tests e2e_tests

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

gui-smoke:
	uv run mailsearch-gui --smoke-test

test:
	uv run pytest -q

test-e2e:
	uv run pytest -q --browser chromium --tracing=retain-on-failure e2e_tests

test-native-gui:
	MAILARCHIVER_NATIVE_GUI_E2E=1 uv run pytest -q e2e_tests/test_ingest_verify.py::test_native_search_ui_end_to_end

test-bagit:
	uv run pytest -q tests/test_bagit.py tests/test_standalone_verify.py

test-mailsearch:
	uv run pytest -q tests/test_mailsearch.py

test-gui:
	uv run pytest -q tests/test_gui_service.py

test-provenance:
	uv run pytest -q tests/test_catalog.py tests/test_sources.py

test-headers:
	uv run pytest -q tests/test_message.py tests/test_search.py

test-progress:
	uv run pytest -q tests/test_progress.py tests/test_sources.py

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
	@mkdir -p "$(TIKA_DIR)"
	curl --fail --location --output "$(TIKA_JAR)" "$(TIKA_URL)"
	curl --fail --location --output "$(TIKA_SHA512)" "$(TIKA_URL).sha512"
	@expected=$$(awk '{print $$1}' "$(TIKA_SHA512)"); actual=$$(shasum -a 512 "$(TIKA_JAR)" | awk '{print $$1}'); test "$$expected" = "$$actual" || { echo "Tika SHA-512 verification failed"; rm -f "$(TIKA_JAR)" "$(TIKA_SHA512)"; exit 1; }
	@echo "Installed and verified $(TIKA_JAR)"
