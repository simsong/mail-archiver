# macOS application and DMG

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

## Build and test

These instructions build the current Python/pywebview application. The compiled
replacement candidates are [Dioxus Desktop and Tauri](DIOXUS.md), with the Python archive
engine bundled alongside it. That migration has not changed `make dmg` or its
validation; no Dioxus DMG is produced by the current build.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

On a logged-in Mac with this checkout's development environment and `uv`:

```sh
make dmg
```

The target installs only project-local packaging dependencies, then uses
PyInstaller to bundle Python, Python extension libraries, Cocoa/WKWebView
bindings, SQL/YAML resources, plug-in manifests, GUI assets, and the standalone
verifier source. It uses the system WebKit, not a downloaded browser. Users
need no Python, `uv`, Homebrew, or source checkout for the supported local-mail
GUI. ClamAV is optional. Experimental PDF extraction/OCR, Tika/Java and the
Apple Intelligence command are not included in this desktop workflow.

The [planned ingest executables](PST_DUAL_READER.md#executables-and-installers)
add selected native helpers, starting with a Rust PST adapter candidate. These
are not included today. Delivery requires nested signing, dependency audits,
notarization/stapling and installed-fixture tests on each supported architecture.
A JVM would be needed only if a Java importer is later bundled; Tika remains
outside the supported desktop runtime.

Schema management requires no Java runtime or external migration tool. SQL files
use Flyway naming conventions only (`V<version>__<description>.sql`); Flyway is
not a dependency and is not bundled. Application-managed SQLite catalog upgrades
remain planned; the current app validates the supported schema and rejects
incompatible catalogs rather than upgrading them.

The initial build uses the native Python architecture (currently arm64), not
universal2. Intel builds require a matching Intel Python and dependencies and
their own validation. Compatibility with older macOS releases must be tested
on those releases; success on the build Mac is not a compatibility matrix.

Output: `dist/Email-Collection-Toolkit-VERSION-ARCH.dmg` with Developer ID
signing, or `dist/Email-Collection-Toolkit-VERSION-ARCH_UNSIGNED.dmg` otherwise. The volume contains
`Email Collection Toolkit.app` on the left and a shortcut to `/Applications` on the right.
A pale-blue background shows the app title, a right-pointing arrow, and
**Drag Email Collection Toolkit to Applications to install**. The 720-by-480-point Finder
window uses large icons and no toolbar/sidebar; there is no separate instruction
file to open. Its 720-by-420-point background leaves room for Finder's window
chrome so the footer stays visible. A build-only `dmgbuild` dependency saves this layout, and the
mounted checks verify it. Use `make preview-dmg DMG=/absolute/path/to/image.dmg`
for visual review in Finder; press Return in the terminal to eject afterward.
The `.mailarchive` package type is declared in
Info.plist; the Cocoa delegate handles document-open events and retains
pywebview's close/ingest safeguards. File Open also accepts extensionless archive
directories. Installation does not force replacement of another default handler.

The build mounts the candidate DMG read-only and runs both self-tests from its
bundled executable, with a system-only PATH and no Python environment overrides.
It verifies the code-signature seal and always attempts to detach the volume.
It also audits every bundled Mach-O file for external non-system library paths.
Ejection retries briefly if macOS still holds the volume; cleanup is nonrecursive
and never traverses a still-mounted filesystem.
Only a passing candidate replaces the prior DMG. JSON test reports sit beside
the final image. Tests create disposable archives and isolated document
preferences; they do not open or import into the user's last archive.

```sh
make self-test
make self-test-gui
make test-packaging
make test-dmg DMG="/absolute/path/to/Email-Collection-Toolkit-0.0.0-arm64.dmg"
```

The first target displays no windows. The second shows and closes the real
About, search, Ingests, and source-picker windows automatically. Both test actual
unscanned ingest, exact-byte retrieval, FTS search, independent preservation
verification, and repeat-import idempotence. The GUI additionally checks native
bridge calls, the missing-scanner banner and canceled source selection.
Each has a watchdog and returns nonzero on failure.

The shipped executable also accepts `--self-test`, `--self-test-gui`, and
`--report /absolute/path/report.json`. Advanced users can access the bundled
archive CLI with `--cli`, without a separately installed Python:

```sh
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test-gui
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --cli --help
```

## Optional antivirus

ClamAV is not bundled or installed automatically. The import-window button
opens the [official downloads page](https://www.clamav.net/downloads). Users
must install and configure it and obtain virus definitions; the official
package alone does not include a working scanner configuration. See
[ClamAV setup](https://docs.clamav.net/manual/Installing.html) and
[signature updates](https://docs.clamav.net/manual/Usage/SignatureManagement.html).
The app detects standard ARM/Intel Homebrew and official macOS installation
paths at launch; environment overrides remain available. Restart after setup.

Missing executables/configuration produce an **Antivirus unavailable** banner.
The import confirmation defaults to Cancel; **Import Without Scanning** is an
explicit opt-out for that import only. It is recorded in run status and in
each new message's antivirus metadata defect. Import history displays the
warning after restart. A configured scanner's startup/scan failure stops import
and never silently opts out. Previously archived messages are not retroactively
scanned by a later ordinary import; a rescan command remains future work.

## Signing and Apple account renewal

On an isolated GitHub-hosted runner, `make dmg` imports a Developer ID Application
identity when both
`APPLE_CERTIFICATE_P12_BASE64` and `APPLE_CERTIFICATE_PASSWORD` are available.
The [certificate management guide](CERTIFICATE_MANAGEMENT.md) explains exporting
the `.p12` and configuring these GitHub Actions repository secrets. The release
workflow passes them to the macOS build and includes the resulting DMG and
checksum in the draft release. The release tag must contain this workflow and
builder; dispatching an older tag does not retrofit the new builder. Release
verification also requires the administrator's `RELEASE_SIGNING_PUBLIC_KEYS`
repository variable. Automatic imports are rejected locally and on self-hosted
runners because `security` password arguments remain visible to other processes;
use an existing keychain identity for local signing.

Missing either secret is nonfatal: the build emits an Actions warning, leaves
the DMG container unsigned, and names it `*_UNSIGNED.dmg`. An explicitly supplied
identity takes precedence; `--signing-identity -` forces the unsigned path.
Both secrets present but invalid is a build failure, not an unsigned fallback.
The imported private key is deleted and the keychain search list restored when
the build exits. Signing still requires a separate notarization step before
claiming normal downloaded-file Gatekeeper acceptance.

Without a Developer ID, PyInstaller and `codesign` use **ad-hoc signing** (`-`).
This makes the bundle internally verifiable; it does not establish trusted
publisher identity or satisfy Gatekeeper/notarization. A self-signed certificate
would not solve that trust problem either. Downloaded builds may be blocked.
Only for a copy the user trusts, macOS provides **System Settings → Privacy &
Security → Open Anyway** after an attempted launch. Never disable Gatekeeper.
The mounted local tests do not establish downloaded-file Gatekeeper acceptance.

1. Sign in with the Apple Account used for the previous developer membership.
   Follow Apple's [renewal instructions](https://developer.apple.com/help/account/membership/renewal/).
   Web enrollment renews through the account; app enrollment uses its subscription.
   If no renewal action is available, contact Apple Developer Support.
2. Once membership is active, obtain a **Developer ID Application** certificate
   and matching private key through Xcode or Certificates, Identifiers & Profiles.
   Renewing membership does not renew an expired signing certificate. Follow
   Apple's [Developer ID instructions](https://developer.apple.com/help/account/certificates/create-developer-id-certificates).
   Developer ID Installer is for `.pkg` installers, not this drag-install DMG.
3. Build with the certificate's Keychain identity:

   ```sh
   make dmg ARGS='--signing-identity "Developer ID Application: YOUR NAME (TEAMID)"'
   ```

4. Before public distribution, submit the DMG with `xcrun notarytool`, inspect
   the accepted result, and staple the ticket with `xcrun stapler`. Follow
   Apple's [notarization workflow](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
   Credentials are not stored in the repository. The Make target does not yet
   automate notarization or claim it has been performed.
