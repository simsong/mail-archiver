# Apple certificate management and signed GitHub DMGs

<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

## Scope and current implementation

This guide covers distributing Email Collection Toolkit from GitHub Releases
using a **Developer ID Application** certificate. The build and release workflow
support optional signing; secrets must still be supplied by a repository admin.

- `make dmg` uses an explicit `--signing-identity` if supplied. Otherwise, it
  imports the `.p12` when both signing secrets described below are present on
  a GitHub-hosted runner. Local and self-hosted automatic imports are rejected;
  local signing uses an existing keychain identity. It signs the nested app code
  and the finished DMG.
- When either secret is absent, it emits a GitHub Actions warning and builds
  `Email-Collection-Toolkit-VERSION-ARCH_UNSIGNED.dmg`. The app remains ad-hoc
  signed; the DMG container has no Developer ID signature.
- If both secrets exist but the Base64, password, certificate, or private key
  is invalid, the build fails. It does not silently publish an unsigned fallback.
- `.github/workflows/release.yml` builds and tests the DMG on `macos-15`, then
  includes it alongside the source archive and SHA-256 checksums in a draft
  release. Both jobs use the Mac job's verified release commit; assembly fails
  if the tag has moved. Before executing project code, the Mac job verifies the
  tag against administrator-configured OpenPGP public keys. The release tag
  must contain this builder/workflow.
- Automatic notarization and stapling remain **unimplemented**. A signed DMG
  alone is not evidence of Gatekeeper acceptance.

See [macOS packaging](MACOS_DISTRIBUTION.md) for features, architecture limits,
and Makefile targets. A successful unit test is not proof of Apple acceptance;
validate a real credentialed release and downloaded artifact before making
that claim.

## 1. Choose the certificate and understand the credentials

| Item | Purpose | Handling |
| --- | --- | --- |
| Developer ID Application | Signs the app and DMG for downloads outside the Mac App Store | Use for this guide |
| Developer ID Installer | Signs `.pkg` installers outside the store | Not needed for our drag-install DMG |
| Apple Distribution / Mac App Distribution | App signing for Mac App Store submission | Separate distribution process |
| Mac Installer Distribution | Installer-package signing for Mac App Store submission | Separate distribution process |
| Downloaded `.cer` | Public certificate binding an identity to a public key | Cannot sign by itself |
| Matching private key | Produces signatures in your identity | Keep secret; generated when creating the CSR |
| Exported `.p12` | Portable certificate and private-key bundle | Encrypt with a strong password; treat as a secret |
| `.p12` password | Unlocks that exported bundle | Different from your Apple Account password |
| Apple notarization credential | Authenticates submissions to Apple's notary service | Separate from the signing key |

Apple documents [Developer ID certificate types and installation](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)
and [distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac).
Your Git commit/tag signing key is also separate: a signed Git tag does not sign
the application, and an Apple signature does not authenticate a Git tag.

### Apple terms and third-party hosting

Apple's [Developer Program License Agreement](https://developer.apple.com/support/terms/apple-developer-program-license-agreement/),
sections 2.9 and 5.1, permits qualifying service providers to act on your behalf
subject to conditions, including a binding agreement with sufficiently
protective terms. You remain responsible for safeguarding certificates and
keys. The agreement expressly restricts transferring App Store distribution
certificates to service providers.

Our reading is that hosted Developer ID signing can fit the service-provider
exception; this is not an explicit Apple endorsement of GitHub Secrets. We have
not verified that your particular GitHub agreement satisfies every Apple
condition. Review the agreement applicable to your membership, and obtain
Apple's clarification if that contractual determination is required before
uploading the private key. Do not carry this interpretation over to App Store
signing automatically.

GitHub supplies a [technical guide for Apple certificates in Actions secrets](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications).
Technical support for the mechanism does not resolve the contractual question.
An alternative is to build, sign, and notarize on your own Mac and upload only
the finished DMG and its checksum to the draft release.

## 2. Install and verify the local signing identity

Use the Mac on which you generated the certificate signing request (CSR).

1. Download the Developer ID Application `.cer` from Apple's developer portal.
2. Double-click it to import it into Keychain Access.
3. In Keychain Access, select the relevant keychain, normally **login**, and
   **My Certificates**. Locate `Developer ID Application: YOUR NAME (TEAMID)`.
4. Expand the certificate. Its matching private key should appear underneath.
5. Check the certificate's validity dates and Team ID.

List usable code-signing identities without exporting private keys:

```sh
security find-identity -v -p codesigning
```

Record the exact Developer ID Application identity name and Team ID. Do not
select an Apple Development or Apple Distribution identity by accident.

If the private key is absent, downloading the `.cer` again will not recreate
it. Locate the original Mac or an encrypted `.p12` backup. If neither exists,
create a new key/CSR and obtain a replacement certificate through Apple.
Do not revoke a working certificate merely to troubleshoot an import problem.

## 3. Export a password-protected `.p12`

In Keychain Access, select the Developer ID Application identity under **My
Certificates**, then use **File → Export Items** (or the context-menu export
command). Choose **Personal Information Exchange (.p12)** and set a strong,
unique export password. Export the identity with its matching private key.
If `.p12` is unavailable, check that you selected an identity containing a key,
rather than only a public certificate.

Alternatively, use Xcode's account settings, select your team, open **Manage
Certificates**, then Control-click the certificate and export it. Apple's
[export instructions](https://help.apple.com/xcode/mac/current/en.lproj/dev154b28f09.html)
describe the password-protected `.p12` output.

Save it outside every source checkout, for example in a private local folder
named `~/Signing`. Keep an encrypted recovery copy and its password in your
chosen secure backup/password-management system. Do not attach the `.p12`, its
password, or notarization credentials to an issue, PR, chat, or release.

The examples below assume the export is named `DeveloperIDApplication.p12`.

## 4. Prepare notarization authentication

For a straightforward personal-account setup:

1. Sign in to [your Apple Account](https://account.apple.com/).
2. Under **Sign-In and Security → App-Specific Passwords**, generate a password
   with a recognizable label such as `mail-archiver GitHub notarization`.
3. Record the Apple Account email and the developer **Team ID** associated with
   the signing certificate. The Team ID is not an App Store application's ID.
4. Store the generated password securely. Never use your normal Apple Account
   login password as a GitHub secret for this workflow.

Two-factor authentication is required for app-specific passwords. Changing
your primary Apple Account password revokes existing app-specific passwords;
update the CI credential afterward. See [Apple's password instructions](https://support.apple.com/en-us/102654).

`notarytool` also supports App Store Connect API-key authentication. That is an
alternative to the email/app-specific-password route, not an additional
required credential. Use Apple's [notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
for its key-type and authentication requirements.

## 5. Put the two signing secrets in GitHub

The installed workflow reads **repository Actions secrets** in
`simsong/email-collection-toolkit`. It does not currently reference a named GitHub
Environment, so secrets stored only in an environment will not reach this job.

1. Export the certificate **and matching private key** as `.p12` as described
   above. A `.cer` file alone cannot be used here.
2. Open the repository on GitHub and choose **Settings → Secrets and variables
   → Actions → New repository secret**.
3. Create `APPLE_CERTIFICATE_P12_BASE64`. To copy the encoded file without
   printing it into terminal output, run on your Mac:

   ```sh
   base64 -i "$HOME/Signing/DeveloperIDApplication.p12" | pbcopy
   ```

4. Paste into the secret value and click **Add secret**. Base64 is an encoding,
   not encryption; the original `.p12` should already have an export password.
5. Create `APPLE_CERTIFICATE_PASSWORD` with that exact export password. This
   is not your Apple Account password or a notarization password.
6. Clear the clipboard after saving:

   ```sh
   pbcopy < /dev/null
   ```

| Repository secret | Required value |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Base64-encoded password-protected `.p12` containing one valid Developer ID Application identity |
| `APPLE_CERTIFICATE_PASSWORD` | Password set when exporting that `.p12` |

No identity-name variable or extra keychain-password secret is needed. The
builder selects the imported identity's fingerprint and generates a temporary
keychain password. The helper omits command arguments and captured tool output
from Python exceptions. **Passwords are still passed in `security` process
arguments and can be read by other processes in the same job.** Automatic import
therefore requires `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`;
the workflow also gates the secret-bearing step on the hosted runner context.
These guards prevent accidental use on shared runners; they do not isolate
secrets from malicious code within the job. Use only trusted build code and
dependencies. The helper removes the imported file, restores the prior keychain
search list, and deletes the temporary keychain on exit. GitHub destroys the
hosted runner after the job. For local signing, import and manage the identity
yourself and supply `--signing-identity`.

GitHub documents the [certificate secret mechanism](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications).
To use environment secrets instead, first modify the Mac job to name that
existing environment, then configure its branch/tag and approval rules. The
current repository-secret setup does not acquire environment approval gates
merely because an environment with the same secrets exists. See
[environment configuration](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

### Configure trusted release tag signers

In **Settings → Secrets and variables → Actions → Variables**, set repository
variable `RELEASE_SIGNING_PUBLIC_KEYS` to the ASCII-armored **public** OpenPGP
keys of the maintainers authorized to sign releases. Verify their full
fingerprints independently before adding them. For example, export a known key
with `gpg --armor --export FULL_FINGERPRINT`; never export its secret key.
Concatenate public-key exports to authorize multiple release signers.

The Mac job imports only these keys into a fresh temporary GnuPG home with
automatic key retrieval disabled, then runs `git verify-tag`. Missing or invalid
configuration, a lightweight tag, or a tag signed by any other key fails before
project commands or Apple secrets are used, including for unsigned DMG builds.
This checks the actual signature, not the tagger's name/email or GitHub's generic
verified badge. GitHub's signature verification and tag/version checks also run.

Signing credentials are available only to the `Build and test DMG` step in
this workflow; they are not needed for PR testing. Anyone who can modify and
execute workflows with repository secrets could extract them. Restrict release
tag creation and workflow changes, review dependencies, and never expose these
secrets to untrusted PR code. The macOS job's actions are pinned to commit SHAs.
The signer gate assumes the workflow itself is trusted: someone able to replace
and execute that workflow with repository secrets can remove the gate. Repository
administrators must restrict workflow changes and release tag creation; this PR
does not configure those remote protections or the public-key variable.
Do not commit `.p12` exports, put passwords in YAML, or upload keychains as
artifacts. Repository secret storage is not a substitute for reviewing Apple's
service-provider conditions in section 1.

## 6. Run and verify DMG production

After the implementation is merged, create the project's normal signed,
annotated release tag containing it, or use **Actions → Assemble draft GitHub
release → Run workflow** with such an existing tag. Follow the repository's
release authorization rules; this document does not authorize publishing a
release or creating a tag. Dispatching an older tag uses its older build code.

The trusted-signer, GitHub signature, and tag/version checks run before project
dependency installation or packaging. The macOS job builds with `make dmg`, verifies the
mounted app, and runs its frozen headless and GUI tests. Any packaging, signing,
or mounted-test failure blocks draft-release assembly. The builder uses the
runner's Python architecture; this is not a universal2 build. Hosted GUI and
real Developer ID signing still need an actual release trial.

| Signing configuration | Expected output |
| --- | --- |
| Both secrets present and valid | `Email-Collection-Toolkit-VERSION-ARCH.dmg`, app and container signed |
| Neither secret present | Warning annotation and `Email-Collection-Toolkit-VERSION-ARCH_UNSIGNED.dmg` |
| Only one secret present | Same warning and unsigned output; the incomplete pair is ignored |
| Both present but invalid | Failed build, no newly uploaded release DMG |

The warning begins `::warning::Developer ID signing skipped` in the build log
and appears as an Actions warning annotation. Absence of signing credentials
is intentionally nonfatal. The unsigned app still has its internal ad-hoc seal
so bundle verification works; `_UNSIGNED` means no trusted Developer ID signature.
Neither path is automatically notarized.

The release assembly job waits for the tested DMG, downloads the `macos-dmg`
artifact, checks out the exact verified commit, builds the source distribution,
and writes `SHA256SUMS` over the source archive and DMG. The JSON packaging
reports are a separate `macos-packaging-reports` Actions artifact. Only explicit
`dist/*.dmg` and `dist/*.json` patterns are uploaded by the Mac job.

For a local build using an identity already in your Keychain:

```sh
make dmg ARGS='--signing-identity "Developer ID Application: YOUR NAME (TEAMID)"'
```

To deliberately produce the unsigned variant locally:

```sh
make dmg ARGS='--signing-identity -'
```

`make test-signing` exercises optional credential selection, warning/naming,
malformed Base64, identity selection, secret-safe failures, and workflow
artifact dependencies through ordered focused lint/type/test stages.
`make test-packaging` also includes those signing regression tests. The tests
do not import your real signing key or establish Apple notarization acceptance.

### Separate notarization after signing

Use section 4 to obtain notarization authentication. This step is manual; the
workflow does not read any notarization secrets. Work on the exact signed DMG
from the build. The following native Apple commands are an operator procedure,
not an additional automated release target:

```sh
DMG="$PWD/dist/Email-Collection-Toolkit-VERSION-ARCH.dmg"
NOTARY_PROFILE='mail-archiver-release'

# Prompts securely for authentication; do not enter passwords in shell history.
xcrun notarytool store-credentials "$NOTARY_PROFILE"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" \
  --wait --timeout 30m --output-format json > "$DMG.notarization.json"
```

Require `status` to be **Accepted** and retain the submission `id`. Upload
success, `In Progress`, or a timeout is not acceptance. Query a pending
submission with `notarytool info`; investigate a rejection with `notarytool log`.
Only after acceptance:

```sh
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
codesign --verify --strict --verbose=2 "$DMG"
spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
make test-dmg DMG="$DMG"
```

Inspect the mounted app with `codesign --display --verbose=4`, checking its
Developer ID authority, Team ID, hardened runtime, and timestamp, and assess it
with `spctl --assess --type execute --verbose=2`. Detach even if a check fails.
Apple describes these operations in its [notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).

Stapling changes the DMG bytes. Replace only the intended draft asset and
regenerate the complete `SHA256SUMS` using the final stapled DMG and source
archive before publication. Do not reuse the pre-stapling checksum or overwrite
a published release casually. A per-file SHA-256 check on macOS is:

```sh
(cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")")
```

For the first notarized release, download through a browser on another supported
Mac, verify its checksum, drag the app to Applications, and launch normally.
Keep quarantine metadata intact and use disposable archive fixtures. A routine
first-download confirmation differs from a developer-verification rejection;
do not disable Gatekeeper or strip quarantine to claim the test passed.

## 7. Renewal, rotation, and recovery

Maintain a private inventory of certificate name, serial/fingerprint, Team ID,
expiration date, original key location, encrypted backup location, and the
GitHub repository/secret names. Record locations and identifiers, not secret
values, in ordinary release notes.

Before expiry, obtain a new Developer ID Application identity, export it, update
the two certificate secrets, and validate a draft build
through notarization and downloaded-app testing. Renewing developer membership
does not itself renew a certificate. Keep recovery material until the new path
works; routine rotation does not require revoking the old identity. Apple's
[Developer ID expiration guidance](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)
explains how already-distributed applications are evaluated.

If the private key may have leaked, stop signing jobs and remove their secret
access, contact Apple promptly about compromise/revocation, and replace the
identity. Audit affected workflow runs and published assets. Changing only the
`.p12` password cannot neutralize a private key already copied by someone else.
Revocation can affect users of previously signed software; coordinate recovery
and replacement releases. See Apple's [certificate support guidance](https://developer.apple.com/help/account/certificates/certificates-overview/).

Rotate the notarization app-specific password separately if it is exposed or
revoked. For recovery on a new Mac, import the encrypted `.p12`, verify that
its private key is present, then repeat a signed build and acceptance checks.

## 8. Troubleshooting

| Symptom | Check and next action |
| --- | --- |
| `0 valid identities found` | Correct keychain unlocked/searchable; certificate has its private key; certificate and trust chain valid |
| `.p12` import fails | Correct export password; file really contains PKCS#12 data; Base64 was copied completely |
| Keychain prompt or signing hangs | Noninteractive signing-key access, partition list, and keychain unlock lifetime |
| App signs but notary service rejects it | Download submission log; inspect the named nested binary, signature, timestamp, hardened runtime, or entitlement problem |
| Authentication fails | Apple Account, Team ID, app-specific password, accepted account agreements, or API-key configuration |
| Notarization times out | Preserve submission ID and query status; do not publish an unaccepted image |
| Stapling fails | Accepted submission, same signed DMG, network access, and Apple's reported ticket availability |
| GUI self-test fails on hosted runner | Actual graphical session and runtime dependencies; preserve diagnostics and block release |
| Download fails checksum | Compare against the final stapled artifact; investigate asset replacement or corruption |
| Gatekeeper rejects an accepted build | Verify the downloaded bytes, ticket, app signature, Team ID, and supported OS; retain quarantine for diagnosis |

Do not resolve signing failures by silently using ad-hoc signing, stripping
quarantine, disabling Gatekeeper, or skipping the mounted tests.
