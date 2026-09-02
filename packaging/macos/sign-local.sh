#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — macOS signing + notarisation (Developer ID, outside App Store)
#
#  USAGE
#    cd sw_artifacts
#    bash packaging/macos/sign-local.sh --app dist/SpendifAi.app \
#         [--dmg build/SpendifAi-X.Y.Z.dmg] [--skip-notarize] [--jobs N]
#
#  ORDER OF OPERATIONS — this is the part that is easy to get wrong
#
#      1. sign the .app         (inside-out, with entitlements)
#      2. BUILD the DMG         ← from the already-signed bundle
#      3. sign the DMG
#      4. notarise, 5. staple
#
#  A DMG captures its contents when it is created. Building it first and
#  signing the app afterwards yields a signed container holding an ad-hoc
#  signed app, and notarisation rejects it — after making you wait for the
#  full round trip. If the bundle was rebuilt, rebuild the DMG too:
#      bash packaging/macos/build-dmg.sh --version X.Y.Z --skip-pyinstaller
#
#  WHY NOT `codesign --deep`
#    Apple discourages it and it is unreliable on bundles with many nested
#    Mach-O binaries — a PyInstaller .app has ~190. We sign inside-out:
#    every nested binary first, deepest first, then the bundle.
#
#  INVARIANTS ON EVERY BINARY
#    --options runtime   hardened runtime; notarisation rejects without it
#    --timestamp         secure timestamp, so the signature outlives the
#                        certificate instead of dying with it
#    --force             overwrites PyInstaller's ad-hoc signature
#
#  CREDENTIALS — never hardcoded, never in the repo
#    APPLE_DEV_ID       "Developer ID Application: Name (TEAMID)"
#                       omit it and the script auto-detects, if exactly one
#                       such identity is in the keychain
#    NOTARY_PROFILE     keychain profile registered once with:
#                         xcrun notarytool store-credentials <name> \
#                            --key <AuthKey.p8> --key-id <ID> --issuer <UUID>
#                       An App Store Connect API key with the `Developer`
#                       role is enough. Fallback: APPLE_ID + APPLE_TEAM_ID +
#                       APPLE_APP_PASSWORD.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

APP_PATH=""
DMG_PATH=""
ENTITLEMENTS="packaging/macos/entitlements.plist"
IDENTITY="${APPLE_DEV_ID:-}"
NOTARY_PROF="${NOTARY_PROFILE:-}"
SKIP_NOTARIZE=false
APP_ONLY=false
JOBS=6

die()  { printf '\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m⚠ %s\033[0m\n' "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)            APP_PATH="$2"; shift 2 ;;
    --dmg)            DMG_PATH="$2"; shift 2 ;;
    --entitlements)   ENTITLEMENTS="$2"; shift 2 ;;
    --notary-profile) NOTARY_PROF="$2"; shift 2 ;;
    --jobs)           JOBS="$2"; shift 2 ;;
    --skip-notarize)  SKIP_NOTARIZE=true; shift ;;
    --app-only)       APP_ONLY=true; SKIP_NOTARIZE=true; shift ;;
    -h|--help)        sed -n '2,46p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -z "${APP_PATH}" && -d "dist/SpendifAi.app" ]] && APP_PATH="dist/SpendifAi.app"
if [[ -z "${DMG_PATH}" && "${APP_ONLY}" == false ]]; then
  CANDIDATE="$(ls -t build/SpendifAi-*.dmg 2>/dev/null | head -1 || true)"
  [[ -n "${CANDIDATE}" ]] && DMG_PATH="${CANDIDATE}"
fi

# bash 3.2 ships with macOS: no `mapfile`, no associative arrays.
if [[ -z "${IDENTITY}" ]]; then
  FOUND="$(security find-identity -v -p codesigning 2>/dev/null \
           | grep "Developer ID Application" | sed -E 's/.*"(.*)"/\1/')"
  N="$(printf '%s' "${FOUND}" | grep -c . || true)"
  case "${N}" in
    0) die "no 'Developer ID Application' identity in the keychain.
   Create one: Xcode → Settings → Accounts → Manage Certificates → +" ;;
    1) IDENTITY="${FOUND}" ;;
    *) die "several Developer ID identities found, set APPLE_DEV_ID:
${FOUND}" ;;
  esac
fi
security find-identity -v -p codesigning | grep -qF "${IDENTITY}" \
  || die "identity not in keychain: ${IDENTITY}"

ENT_ARGS=()
if [[ -f "${ENTITLEMENTS}" ]]; then
  plutil -lint "${ENTITLEMENTS}" >/dev/null || die "malformed entitlements"
  ENT_ARGS=(--entitlements "${ENTITLEMENTS}")
elif [[ -n "${APP_PATH}" ]]; then
  warn "no entitlements file: under the hardened runtime a frozen Python
   bundle signs and notarises fine and then CRASHES on launch."
fi

# ── 1. Sign the bundle, inside-out ──────────────────────────────────────────
if [[ -n "${APP_PATH}" ]]; then
  [[ -d "${APP_PATH}" ]] || die "bundle not found: ${APP_PATH}"
  MAIN_EXE="${APP_PATH}/Contents/MacOS/$(/usr/libexec/PlistBuddy \
      -c 'Print :CFBundleExecutable' "${APP_PATH}/Contents/Info.plist" 2>/dev/null || echo '')"

  step "Collecting nested Mach-O binaries"
  # Detect by file type, not extension: many have neither .so nor .dylib.
  # -type f skips framework symlinks — signing a symlink breaks the bundle.
  NESTED="$(mktemp)"; trap 'rm -f "${NESTED}"' EXIT
  find "${APP_PATH}" -type f \( -name '*.so' -o -name '*.dylib' -o -perm +111 \) -print0 \
    | xargs -0 file --mime-type 2>/dev/null \
    | grep -E 'application/x-mach-binary' | cut -d: -f1 \
    | grep -vxF "${MAIN_EXE}" \
    | awk '{ print gsub(/\//,"/") "\t" $0 }' | sort -rn | cut -f2- > "${NESTED}"
  COUNT=$(wc -l < "${NESTED}" | tr -d ' ')
  echo "  ${COUNT} nested binaries, deepest first"

  step "Signing nested binaries (${JOBS} parallel)"
  # Entitlements belong to the bundle, not to the nested binaries.
  # Each --timestamp is a round trip to Apple, hence the parallelism.
  export IDENTITY
  tr '\n' '\0' < "${NESTED}" | xargs -0 -P "${JOBS}" -n 1 -I{} \
    /bin/sh -c 'codesign --force --options runtime --timestamp --sign "$IDENTITY" "$1" 2>&1 \
                | grep -v "replacing existing signature" || true' _ {}
  ok "${COUNT} nested binaries signed"

  step "Signing the bundle"
  codesign --force --options runtime --timestamp "${ENT_ARGS[@]}" \
           --sign "${IDENTITY}" "${APP_PATH}"

  step "Verifying"
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
  codesign -dv --verbose=2 "${APP_PATH}" 2>&1 | grep -E 'Authority|TeamIdentifier|flags' || true
  spctl --assess --type execute --verbose "${APP_PATH}" \
    || warn "spctl not satisfied yet — expected until notarised"
  ok "${APP_PATH} signed"

  if [[ -n "${DMG_PATH}" && "${DMG_PATH}" -ot "${APP_PATH}/Contents/MacOS" ]]; then
    warn "${DMG_PATH} is OLDER than the bundle you just signed: it still
   contains the unsigned copy and notarisation would reject it. Rebuild it:
     bash packaging/macos/build-dmg.sh --skip-pyinstaller"
  fi
fi

# ── 2. Sign the container ───────────────────────────────────────────────────
if [[ -n "${DMG_PATH}" ]]; then
  [[ -f "${DMG_PATH}" ]] || die "DMG not found: ${DMG_PATH}"
  # Re-signing a stapled DMG silently invalidates its notarisation ticket.
  if xcrun stapler validate "${DMG_PATH}" >/dev/null 2>&1; then
    die "${DMG_PATH} is already notarised and stapled.
   Re-signing it would invalidate the ticket. Rebuild the DMG first, or pass
   an explicit --dmg path if you really mean to sign a different file."
  fi
  step "Signing ${DMG_PATH}"
  codesign --force --timestamp --sign "${IDENTITY}" "${DMG_PATH}"
  ok "${DMG_PATH} signed"
fi

# ── 3. Notarise ─────────────────────────────────────────────────────────────
if [[ "${SKIP_NOTARIZE}" == true ]]; then
  warn "notarisation skipped (--skip-notarize): NOT distributable"; exit 0
fi
[[ -z "${DMG_PATH}" ]] && { warn "no container to notarise"; exit 0; }

NOTARY_ARGS=()
if [[ -n "${NOTARY_PROF}" ]]; then
  NOTARY_ARGS=(--keychain-profile "${NOTARY_PROF}")
elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" ]]; then
  NOTARY_ARGS=(--apple-id "${APPLE_ID}" --team-id "${APPLE_TEAM_ID}" --password "${APPLE_APP_PASSWORD}")
else
  die "set NOTARY_PROFILE, or APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PASSWORD"
fi

step "Submitting ${DMG_PATH} to Apple (5-15 min, blocking)"
SUBMIT_OUT="$(mktemp)"
xcrun notarytool submit "${DMG_PATH}" "${NOTARY_ARGS[@]}" --wait 2>&1 | tee "${SUBMIT_OUT}"
if ! grep -q "status: Accepted" "${SUBMIT_OUT}"; then
  # On rejection the submission log is the only output that says why.
  ID="$(grep -oE '[0-9a-f-]{36}' "${SUBMIT_OUT}" | head -1 || true)"
  [[ -n "${ID}" ]] && xcrun notarytool log "${ID}" "${NOTARY_ARGS[@]}" || true
  die "notarisation not accepted"
fi

step "Stapling the ticket"
xcrun stapler staple "${DMG_PATH}"
xcrun stapler validate "${DMG_PATH}"
ok "${DMG_PATH} signed + notarised + stapled"

cat <<EOM

Verify on a DOWNLOADED copy, not this one — this file has no quarantine flag:
  spctl -a -t open --context context:primary-signature <downloaded.dmg>
Expected: accepted / source=Notarized Developer ID
EOM
