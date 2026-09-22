# Spendif.ai — Release Process

This document covers the full release pipeline: versioning policy, checklist,
macOS code signing, Homebrew, winget, and future CI/CD automation.

---

## 1. Semantic Versioning Policy

Spendif.ai follows [Semantic Versioning 2.0.0](https://semver.org/) — `MAJOR.MINOR.PATCH`.

| Bump | When to use | Examples |
|------|-------------|---------|
| `PATCH` | Bug fixes, dependency updates, doc corrections. No new features, no breaking changes. | Fix CSV parser crash, update llama.cpp binding |
| `MINOR` | New features that are backward-compatible. New bank importers, new LLM adapters, new UI pages. | Add BancoBPM importer, add Gemma4 support |
| `MAJOR` | Breaking changes: database schema migration required, config file format change, removal of supported instruments. | Migrate SQLite → DuckDB, rename config keys |

The authoritative version is the `VERSION` file at the repo root.

`packaging/release.sh` writes **only** that file — it does not propagate the
version anywhere else. The remaining references have to be bumped in the same
release commit, by hand until the script is extended:

| Reference | Why it matters |
|---|---|
| `pyproject.toml` | package metadata |
| `core/_build_info.py` | version shown in the sidebar; regenerated at build time by the macOS and Windows builders, but **not** by `build-deb.sh` / `build-rpm.sh`, so the Linux packages ship the committed value |
| `packaging/winget/SpendifAi.SpendifAi.*` | winget manifests |
| `packaging/homebrew/spendifai.rb` | cask template — `version`/`sha256` are then rendered into the tap by `packaging/homebrew/update-tap.sh` (Section 3) |
| `uv.lock` | pins the project's own version. `ci.yml` runs `uv sync --frozen`, which refuses a lockfile that disagrees with `pyproject.toml`, so forgetting this one turns the whole CI red. Run `uv lock` after bumping `pyproject.toml`: with no dependency change it rewrites a single line |

---

## 2. Release Checklist

### Pre-release

- [ ] All planned issues for the milestone are closed or deferred
- [ ] `CHANGELOG.md` updated with a `## [X.Y.Z] - YYYY-MM-DD` section
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Manual smoke test: import a CSV, run categorisation, check dashboard
- [ ] No uncommitted changes (`git status` clean)
- [ ] On `main` branch and up to date with remote
- [ ] `gh auth status` confirms authentication with `drake69` account

### Build & publish

```bash
# Dry run first — verify all steps without side effects
bash packaging/release.sh --patch --dry-run

# Actual release
bash packaging/release.sh --patch
```

The script handles: version bump, DMG build, ZIP build, manifest JSON,
git commit + tag + push, GitHub release creation, Homebrew tap update,
winget manifest generation.

### Post-release

- [ ] Verify GitHub release page: https://github.com/spendifai/spendif-ai/releases
- [ ] Download and test DMG on a clean macOS machine
- [ ] Submit winget PR (see Section 5)
- [ ] Update landing page version number if hardcoded
- [ ] Announce on relevant channels

---

## 2bis. CI-driven release with hybrid local signing (recommended)

The workflow `.github/workflows/release.yml` automates building the four
installers (DMG, MSIX, .deb, .rpm) on every `v*.*.*` tag and creates a
**draft** GitHub Release with the unsigned artefacts attached. The owner
then signs DMG and MSIX **locally** with their certificates and replaces
the unsigned files before publishing. This keeps signing credentials off
GitHub while still automating the build.

### Step-by-step

```bash
# 1. Bump VERSION, update CHANGELOG, commit, tag, push
echo "3.1.0" > VERSION
git add VERSION CHANGELOG.md
git commit -m "chore(release): bump to 3.1.0"
git tag v3.1.0
git push origin main v3.1.0
```

Once the tag lands, the CI runs four parallel build jobs (~15–25 min for
the macOS job, ~10–15 min for the Windows job) and produces a **draft**
release. Then, on the owner's machines:

```bash
# 2a. macOS — sign + notarise
#
# A DMG captures its contents when it is created, so signing the container
# CI produced is not enough: the app inside it is still ad-hoc signed and
# notarisation rejects the submission. Extract the app, sign it, repackage.
gh release download v3.1.0 --pattern '*.dmg' --dir /tmp/release

MP=$(hdiutil attach /tmp/release/SpendifAi-3.1.0-arm64.dmg -nobrowse -readonly \
     | grep -o '/Volumes/.*' | head -1)
rm -rf dist/SpendifAi.app && cp -R "$MP/SpendifAi.app" dist/
hdiutil detach "$MP" -quiet

# Credentials: register the notarytool profile once (App Store Connect API
# key with the `Developer` role is enough — see below), then it is implicit.
export NOTARY_PROFILE=spendifai-notary

bash packaging/macos/sign-local.sh --app dist/SpendifAi.app --skip-notarize
bash packaging/macos/build-dmg.sh --version 3.1.0 --skip-pyinstaller
bash packaging/macos/sign-local.sh --dmg build/SpendifAi-3.1.0-arm64.dmg

# Verify on a copy carrying the quarantine flag, never on the built file
cp build/SpendifAi-3.1.0-arm64.dmg /tmp/downloaded.dmg
xattr -w com.apple.quarantine "0083;00000000;Safari;" /tmp/downloaded.dmg
spctl -a -t open --context context:primary-signature -v /tmp/downloaded.dmg
# expected: accepted / source=Notarized Developer ID

gh release upload v3.1.0 build/SpendifAi-3.1.0-arm64.dmg --clobber
```

```bash
# 2b. Windows - sign the MSIX, from macOS, no Windows machine needed
#
# The OV certificate has no .pfx: since 2023 the private key lives on an HSM
# and every use goes through the provider's client. Signing therefore stays
# local and manual, like the Apple credentials, and never runs in CI.
#
# The vendor client mis-handles MSIX: given one, it signs it as a jar, exits
# 0, and returns a package Windows reads as unsigned. sign_appx.sh hands the
# same remote key to the APPX signer instead. See the codesign README in the
# blueprint tooling for the whole story.
gh release download v3.1.0 --pattern '*.msix' --dir /tmp/release

bash ~/Documents/Progetti/blueprint/sw_artifacts/tools/codesign/sign_appx.sh \
  --input  /tmp/release/SpendifAi-3.1.0.msix \
  --output /tmp/release/SpendifAi-3.1.0-signed.msix \
  --program-name "Spendif.ai" \
  --program-url  "https://spendif.ai" \
  --creds ~/secrets/spendifai/windows/codesign.env

# The script verifies before returning: AppxSignature.p7x must be present and
# all four APPX hashes must match. Do not upload a package that failed either.
mv /tmp/release/SpendifAi-3.1.0-signed.msix /tmp/release/SpendifAi-3.1.0.msix
gh release upload v3.1.0 /tmp/release/SpendifAi-3.1.0.msix --clobber
```

The publisher DN in the package manifest must equal the certificate subject,
which means the build has to know it: `MSIX_PUBLISHER` is a repository secret
and the build fails without it. Its spelling is not obvious and is documented
in `build-msix.ps1`; the short version is `S=` for stateOrProvince and a comma
plus a space between components.

```bash
# 3. Recompute SHA256SUMS.txt to include the signed files
gh release download v3.1.0 --dir /tmp/release
cd /tmp/release && sha256sum *.dmg *.msix *.deb *.rpm > SHA256SUMS.txt
gh release upload v3.1.0 SHA256SUMS.txt --clobber

# 4. Publish the draft
gh release edit v3.1.0 --draft=false
```

### Notarisation credentials — one-time setup

The `.p8` App Store Connect API key is preferred over an app-specific password:
it also covers store uploads, it does not expire, and it can be revoked on its
own. Create it in App Store Connect → *Users and Access* → *Integrations*, as a
**Team Key** with the **`Developer`** role (verified sufficient for
notarisation), then register it once:

```bash
xcrun notarytool store-credentials spendifai-notary \
    --key ~/secrets/apple/spendifai-notary.p8 \
    --key-id <KEYID> --issuer <ISSUER-UUID>
```

The key is downloadable **once**. Keep it outside any repository and `chmod 600`
it — browsers save it world-readable.

### Why hybrid

| Concern | Pure CI signing | Local signing | Hybrid (this one) |
|---------|-----------------|---------------|-------------------|
| Build automation | ✓ | ✗ | ✓ (CI) |
| Certs off GitHub | ✗ | ✓ | ✓ |
| One-click release | ✓ | ✗ | ✗ (manual sign step) |
| Risk if token leaks | Signed malware | None | None |

Suitable for solo founders or small teams that already have the certs
locally and prefer not to upload them as Secrets.

---

## 3. Homebrew Distribution

### Homebrew Tap (current approach)

The tap repository `spendifai/homebrew-spendifai` (separate from the main code repo)
holds a single cask file at `Casks/spendifai.rb`. The template lives here, in
`packaging/homebrew/spendifai.rb`.

User installation:
```bash
brew tap spendifai/spendifai
brew trust --cask spendifai/spendifai/spendifai
brew install --cask spendifai
```

`brew trust` is required once per machine: Homebrew 6 refuses to load casks from
third-party taps until the user vouches for them; it is unrelated to code
signing. Since 0.2.1 the DMG is signed and notarised, so `--no-quarantine` is no
longer needed. Subsequent releases:

```bash
brew update && brew upgrade --cask spendifai
```

The cask carries a `livecheck` block with the `github_latest` strategy, so
`brew upgrade` picks up new releases without any extra plumbing.

#### Publishing a release to the tap

**This is automatic.** `.github/workflows/publish-tap.yml` runs on the
`release: published` event and pushes the cask for you. There is nothing to do
by hand in the normal flow.

The trigger is the publish event, not the version tag, and that is deliberate.
The tag fires while the release is still a draft holding the **unsigned** CI
DMG (Section 2bis). A tag-triggered job would write that file's sha256 into the
cask, and every user would then get a checksum mismatch from brew on a download
that is perfectly fine.

Prerequisite, once: the secret `TAP_PUSH_TOKEN` on `spendifai/spendif-ai`.
`GITHUB_TOKEN` cannot write to another repository, and the cask lives in
`spendifai/homebrew-spendifai`. Create a fine-grained personal access token
with resource owner `spendifai`, access to both `spendifai/homebrew-spendifai`
and `spendifai/spendif-ai`, and `Contents: Read and write`. Without it the
workflow fails loudly rather than skipping: a tap that quietly stops being
updated is the exact failure this workflow exists to prevent.

To run it by hand (a re-publish, or a release that predates the workflow), use
`workflow_dispatch` on the Actions tab, or the script directly:

```bash
bash packaging/homebrew/update-tap.sh                 # version from the VERSION file
bash packaging/homebrew/update-tap.sh --version 0.2.0 # or explicit
bash packaging/homebrew/update-tap.sh --dry-run       # inspect first
bash packaging/homebrew/update-tap.sh --verify-hash   # what CI passes; see below
```

The script reads the DMG checksum from the release's `SHA256SUMS.txt` (falling
back to downloading the DMG and hashing it), renders the template, creates the
tap repository if it does not exist yet, and pushes `Casks/spendifai.rb` plus a
generated README. It is idempotent: re-running it for the same version is a
no-op.

`--verify-hash` downloads the published DMG and cross-checks it against
`SHA256SUMS.txt`, failing if they disagree. CI always passes it, because
`SHA256SUMS.txt` is written by the publish job from the UNSIGNED artefacts and
only step 3 of Section 2bis regenerates it after signing. Skip that step and
the sums file still describes the unsigned DMG: the cask would then ship a
checksum no user can ever match. Hashing the published asset is the only source
that cannot be stale.

Note: `packaging/release.sh` does **not** touch the tap, despite what earlier
revisions of this document claimed.

### Homebrew Core (future)

Homebrew Core is the official, curated repository. Requirements to be accepted:

- **Popularity**: ≥75 GitHub stars at time of submission
- **Stable release**: ≥1 stable (non-pre-release) version with a tagged release
- **Signed and notarised app**: The macOS `.app` must be signed with an Apple
  Developer ID certificate and notarised by Apple (see Section 4)
- **No phone-home**: The app must not check for updates or send telemetry at launch
- **Reproducible URL**: The download URL must be stable and point to a versioned
  GitHub release asset (not `latest`)

Submission process for Homebrew Core:
1. Fork `https://github.com/Homebrew/homebrew-cask`
2. Add `Casks/s/spendifai.rb` (alphabetical subdirectory)
3. Run `brew audit --cask spendifai` and `brew install --cask spendifai` locally
4. Open a pull request — the Homebrew CI (GitHub Actions) validates automatically
5. A Homebrew maintainer reviews and merges (typically 1–4 weeks)

---

## 4. macOS Code Signing and Notarisation

Without code signing, macOS Gatekeeper blocks the app on first launch with
"Spendif.ai cannot be opened because the developer cannot be verified."
Signing is **required** for Homebrew Core and strongly recommended for
general distribution.

### Prerequisites

- Apple Developer Program membership (€99/year): https://developer.apple.com/programs/
- Xcode or Xcode Command Line Tools installed
- A "Developer ID Application" certificate downloaded to your keychain

### Sign the .app bundle

```bash
# List available signing identities
security find-identity -v -p codesigning

# Sign (replace TEAM_ID with your 10-character Apple Team ID)
codesign \
  --deep \
  --force \
  --verify \
  --verbose \
  --sign "Developer ID Application: Your Name (TEAM_ID)" \
  --options runtime \
  --entitlements packaging/macos/entitlements.plist \
  build/Spendif.ai.app

# Verify
codesign --verify --deep --strict --verbose=2 build/Spendif.ai.app
spctl --assess --type execute --verbose build/Spendif.ai.app
```

A minimal `entitlements.plist` for a Streamlit/Python app:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-jit</key><false/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
```

### Notarise the DMG

```bash
# Submit DMG to Apple notary service (requires App Store Connect API key)
xcrun notarytool submit build/Spendif.ai-0.1.0.dmg \
  --apple-id "your@apple.id" \
  --team-id "TEAM_ID" \
  --password "@keychain:AC_PASSWORD" \
  --wait

# Staple the notarisation ticket to the DMG
xcrun stapler staple build/Spendif.ai-0.1.0.dmg

# Verify
xcrun stapler validate build/Spendif.ai-0.1.0.dmg
spctl --assess --type open --context context:primary-signature -v build/Spendif.ai-0.1.0.dmg
```

The `packaging/release.sh` script has placeholder hooks for signing — look for
`# SIGN_APP` and `# NOTARISE_DMG` comments to enable when credentials are
configured.

---

## 5. winget Submission

winget (Windows Package Manager) is Microsoft's official package manager.
Packages live in the community repository `microsoft/winget-pkgs`.

### One-time setup

```powershell
# Install winget (bundled with Windows 10 1809+ / App Installer)
# Verify
winget --version
```

On macOS/Linux for testing manifests:
```bash
# Install the winget validation tool
pip install winget-manifest-validator  # community tool, not official
```

### Per-release submission

After `packaging/release.sh` runs, manifests are at:
```
build/winget/manifests/d/SpendifAi/SpendifAi/<version>/
  SpendifAi.SpendifAi.yaml
  SpendifAi.SpendifAi.installer.yaml
  SpendifAi.SpendifAi.locale.en-US.yaml
```

Steps to submit:

1. **Fork** `https://github.com/microsoft/winget-pkgs` (one-time)

2. **Create a branch** in your fork:
   ```bash
   git checkout -b SpendifAi.SpendifAi-<version>
   ```

3. **Copy manifests** to the correct path in the fork:
   ```bash
   mkdir -p manifests/d/SpendifAi/SpendifAi/<version>
   cp build/winget/manifests/d/SpendifAi/SpendifAi/<version>/* \
      manifests/d/SpendifAi/SpendifAi/<version>/
   ```

4. **Validate locally** (optional but recommended):
   ```bash
   winget validate --manifest manifests/d/SpendifAi/SpendifAi/<version>/
   ```

5. **Push and open a PR** against `microsoft/winget-pkgs main`

6. **Bot validation**: The `winget-bot` will:
   - Download and install the package in a sandboxed VM
   - Run automated tests
   - Comment with pass/fail results

   Address any failures before maintainers review.

7. **Merge**: Once approved, the package is available within ~24 hours:
   ```powershell
   winget install SpendifAi.SpendifAi
   ```

### Updating an existing version

winget does not allow modifying published manifests. For a new version, add a
new version directory — do not modify the old one.

---

## 5b. Linux Packages (.deb / .rpm)

### .deb (Ubuntu / Debian / Mint)

Build script: `packaging/linux/build-deb.sh`

```bash
cd sw_artifacts
bash packaging/linux/build-deb.sh              # uses VERSION file
bash packaging/linux/build-deb.sh --version 1.2.3  # explicit version
```

Produces: `build/spendifai_<version>_amd64.deb`

The `.deb` installs source code to `/opt/spendifai/`. The `postinst` script:
1. Installs `uv` if not present
2. Runs `uv sync --extra desktop` to create the Python venv
3. Detects GPU (NVIDIA CUDA)
4. Downloads the recommended AI model from HuggingFace
5. Configures `.env` with `LLM_BACKEND=local_llama_cpp`
6. Registers the `.desktop` launcher and updates the icon cache

Dependencies declared in `Depends:`: `python3`, `python3-venv`, `python3-dev`, `python3-gi`, `gir1.2-webkit2-4.1`, `git`, `curl`, `gcc`, `cmake`, `pkg-config`.

Install/uninstall:
```bash
sudo apt install ./build/spendifai_0.1.0_amd64.deb
sudo apt remove spendifai       # removes code, preserves ~/.spendifai/
```

### .rpm (Fedora / RHEL / Rocky / Alma)

Build script: `packaging/linux/build-rpm.sh`

```bash
cd sw_artifacts
bash packaging/linux/build-rpm.sh              # uses VERSION file
bash packaging/linux/build-rpm.sh --version 1.2.3
```

Requires: `rpm-build` (`sudo dnf install rpm-build`)

Produces: `build/spendifai-<version>-1.noarch.rpm`

Same post-install logic as the `.deb`. Dependencies: `python3`, `python3-devel`, `python3-gobject`, `webkit2gtk4.1`, `git`, `curl`, `gcc`, `cmake`.

Install/uninstall:
```bash
sudo dnf install ./build/spendifai-0.1.0-1.noarch.rpm
sudo dnf remove spendifai
```

### APT repository (signed)

`sudo apt install ./spendifai_*.deb` installs a file. It is not a repository:
no `apt update`, no `apt upgrade`, no signature. The signed archive at
`spendifai/apt`, served by GitHub Pages, is what makes the .deb behave like any
other package on the system.

**Publishing a release.** After the GitHub Release is published:

```bash
python3 packaging/linux/update-apt-repo.py                 # version from the VERSION file
python3 packaging/linux/update-apt-repo.py --version 0.2.2 # or explicit
python3 packaging/linux/update-apt-repo.py --dry-run       # build the index, push nothing
```

The script downloads the .deb assets, rebuilds `Packages` and `Release` from
everything in the pool (so older versions stay installable), signs `InRelease`
and `Release.gpg`, exports the public key dearmored, and pushes. It keeps the
newest 5 versions in the pool; the GitHub Release keeps every version anyway.

**Why this one is NOT automated in CI, unlike the Homebrew tap.** A repository
signing key that leaks lets an attacker serve arbitrary packages to everyone
who added the repository: the same damage as a leaked code signing key, and the
same reason Section 2bis keeps the Apple credentials off CI. The key stays in
`~/secrets/spendifai/gpg/`, and `.github/workflows/apt-repo.yml` has a `verify`
job that runs after every release and weekly, failing when the published
archive falls behind the newest release. Forgetting is loud rather than silent.

**One-time setup.**

```bash
# 1. The signing key. Kept out of any repository, like every other credential.
mkdir -p ~/secrets/spendifai/gpg && chmod 700 ~/secrets/spendifai/gpg
gpg --full-generate-key         # ed25519, sign only, no expiry, a real passphrase

# 2. Back it up. Losing it means every user has to re-add a new key by hand.
gpg --export-secret-keys --armor <KEYID> > ~/secrets/spendifai/gpg/signing-key.asc
gpg --gen-revoke <KEYID> > ~/secrets/spendifai/gpg/revocation.asc
chmod 600 ~/secrets/spendifai/gpg/*

# 3. First publish, which also creates the repository.
python3 packaging/linux/update-apt-repo.py --gpg-key <KEYID>

# 4. Enable Pages once, from the default branch root:
#    https://github.com/spendifai/apt/settings/pages
```

On expiry: generate the key with **no expiry date**. An expired signing key
breaks `apt update` for every user at once, on a date nobody wrote down. The
revocation certificate from step 2 is what covers the compromise case.

**Known limitation.** The package declares `Depends: python3 (>= 3.12)`.
Ubuntu 24.04 and Debian 13 satisfy it; **Debian 12 ships Python 3.11 and cannot
install it**. apt reports the unmet dependency rather than installing something
broken, but the repository is still offered to machines that cannot use it.

### Interactive installers (no package manager)

For users who prefer not to use .deb/.rpm:
- Ubuntu/Debian: `bash packaging/linux/install-debian.sh`
- Red Hat/Fedora: `bash packaging/linux/install-redhat.sh`

Both scripts install to `~/.local/share/Spendif.ai/` (no sudo required for the code, only for system packages).

---

## 6. GitHub Actions CI/CD (implemented)

The workflow `.github/workflows/release.yml` is live and produces all four
installers on every `v*.*.*` tag. See **Section 2bis** for the hybrid
signing workflow that wraps it.

Jobs:

| Job | Runner | Produces | Notes |
|-----|--------|----------|-------|
| `build-macos` | `macos-latest` | `SpendifAi-<ver>-arm64.dmg` (unsigned, arm64 only) | PyInstaller + `create-dmg` |
| `build-windows` | `windows-latest` | `SpendifAi-<ver>.msix` (unsigned) | PyInstaller + `makeappx.exe`. See `packaging/windows/build-msix.ps1`. |
| `build-deb` | `ubuntu-latest` | `spendifai_<ver>_amd64.deb` | Smoke-tested in `ubuntu:24.04` container |
| `build-rpm` | `ubuntu-latest` | `spendifai-<ver>-1.noarch.rpm` | Smoke-tested in `fedora:41` container |
| `publish` | `ubuntu-latest` | Draft GitHub Release + `SHA256SUMS.txt` | Notes extracted from `CHANGELOG.md` |

Older proposal kept below for reference — describes the path we did **not**
take (signing inside CI with secrets):

```yaml
# .github/workflows/release.yml
on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install create-dmg
        run: brew install create-dmg
      - name: Generate icon
        run: python3 packaging/macos/create_icon.py
      - name: Build .app and DMG
        run: bash packaging/release.sh --skip-zip  # DMG only on macOS runner
      - name: Sign and notarise
        env:
          APPLE_CERT_BASE64: ${{ secrets.APPLE_CERT_BASE64 }}
          APPLE_CERT_PASSWORD: ${{ secrets.APPLE_CERT_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_APP_PASSWORD: ${{ secrets.APPLE_APP_PASSWORD }}
        run: bash packaging/macos/sign_and_notarise.sh
      - uses: actions/upload-artifact@v4
        with:
          name: dmg
          path: build/*.dmg

  build-windows-zip:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build ZIP
        run: bash packaging/release.sh --skip-dmg  # ZIP only
      - uses: actions/upload-artifact@v4
        with:
          name: zip
          path: build/*.zip

  publish:
    needs: [build-macos, build-windows-zip]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - name: Create GitHub release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION=$(cat VERSION | tr -d '[:space:]')
          gh release create "v${VERSION}" \
            --title "Spendif.ai v${VERSION}" \
            --generate-notes \
            dmg/*.dmg zip/*.zip
      - name: Update Homebrew tap
        env:
          TAP_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
        run: bash packaging/ci/update_homebrew_tap.sh
      - name: Generate winget manifests
        run: bash packaging/ci/generate_winget.sh
```

Secrets required:
- `APPLE_CERT_BASE64` — base64-encoded .p12 Developer ID certificate
- `APPLE_CERT_PASSWORD` — .p12 password
- `APPLE_TEAM_ID`, `APPLE_ID`, `APPLE_APP_PASSWORD` — for notarytool
- `HOMEBREW_TAP_TOKEN` — GitHub PAT with write access to homebrew-spendifai
- `GITHUB_TOKEN` — automatically provided by Actions

This is a future goal; the current manual `release.sh` approach is sufficient
for a solo founder making infrequent releases.
