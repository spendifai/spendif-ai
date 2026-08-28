#!/usr/bin/env bash
# =============================================================================
# packaging/release.sh — Spendif.ai release trigger
# =============================================================================
#
# Bumps VERSION, commits, tags vX.Y.Z, pushes main + tag. The push of the tag
# triggers .github/workflows/release.yml which builds DMG (macOS), MSIX
# (Windows), .deb (amd64/arm64), .rpm (Fedora), computes SHA256SUMS, and opens
# a DRAFT GitHub Release with all artifacts attached.
#
# The owner then signs DMG + MSIX locally (hybrid signing — see
# docs/release_process.md), uploads the signed binaries with
# `gh release upload <tag> <file> --clobber`, and publishes the draft.
#
# USAGE
#   bash packaging/release.sh [--major|--minor|--patch] [--dry-run]
#
# PREREQUISITES
#   git
#   Push access to origin/main
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUMP_TYPE="patch"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --major) BUMP_TYPE="major" ;;
    --minor) BUMP_TYPE="minor" ;;
    --patch) BUMP_TYPE="patch" ;;
    --dry-run) DRY_RUN=true ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: bash packaging/release.sh [--major|--minor|--patch] [--dry-run]"
      exit 1
      ;;
  esac
  shift
done

info()  { echo "▶  $*"; }
ok()    { echo "✅ $*"; }
err()   { echo "❌ $*" >&2; exit 1; }

run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

# Step 1 — Read current version
VERSION_FILE="${REPO_ROOT}/VERSION"
[[ -f "${VERSION_FILE}" ]] || err "VERSION file not found at ${VERSION_FILE}"

CURRENT_VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
info "Current version: ${CURRENT_VERSION}"

IFS='.' read -r MAJOR MINOR PATCH <<< "${CURRENT_VERSION}"

# Step 2 — Bump version
case "${BUMP_TYPE}" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
info "New version: ${NEW_VERSION} (bump: ${BUMP_TYPE})"

# Step 3 — Git checks
cd "${REPO_ROOT}"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "${CURRENT_BRANCH}" == "main" ]] || err "Must be on 'main' branch (currently on '${CURRENT_BRANCH}')"

if ! git diff --quiet || ! git diff --cached --quiet; then
  err "There are uncommitted changes. Commit or stash them before releasing."
fi

# Refuse to overwrite an existing tag
if git rev-parse "v${NEW_VERSION}" >/dev/null 2>&1; then
  err "Tag v${NEW_VERSION} already exists. Bump again or delete the tag."
fi

UNPUSHED=$(git log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' ')
if [[ "${UNPUSHED}" -gt 0 ]]; then
  info "Pushing ${UNPUSHED} unpushed commit(s) to origin..."
  run git push origin main
fi

ok "Git state clean. HEAD: $(git rev-parse --short HEAD)"

# Step 4 — Write VERSION, commit, tag, push
info "Committing version bump and pushing tag..."

if ! $DRY_RUN; then
  printf '%s\n' "${NEW_VERSION}" > "${VERSION_FILE}"
  git add "${VERSION_FILE}"

  # CHANGELOG is optional but conventionally updated alongside VERSION
  if ! git diff --quiet "${REPO_ROOT}/CHANGELOG.md" 2>/dev/null; then
    git add "${REPO_ROOT}/CHANGELOG.md"
  fi

  git commit -m "chore: release v${NEW_VERSION}"
  git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
  git push origin main
  git push origin "v${NEW_VERSION}"
  ok "Committed and tagged v${NEW_VERSION}"
else
  echo "[DRY-RUN] Would write VERSION=${NEW_VERSION}"
  echo "[DRY-RUN] Would commit: chore: release v${NEW_VERSION}"
  echo "[DRY-RUN] Would create annotated tag v${NEW_VERSION}"
  echo "[DRY-RUN] Would push main and tag to origin"
fi

# Step 5 — Final summary
echo ""
echo "============================================================"
echo "  Spendif.ai v${NEW_VERSION} — tag pushed"
echo "============================================================"
echo ""
echo "  CI is now building DMG / MSIX / .deb / .rpm."
echo "  Watch:    https://github.com/spendifai/spendif-ai/actions"
echo "  Release:  https://github.com/spendifai/spendif-ai/releases/tag/v${NEW_VERSION}  (DRAFT)"
echo ""
echo "Next steps once CI completes:"
echo "  1. Sign DMG locally:   bash packaging/macos/sign-local.sh"
echo "  2. Sign MSIX locally:  pwsh packaging/windows/sign-local.ps1"
echo "  3. Upload signed:      gh release upload v${NEW_VERSION} <file> --clobber"
echo "  4. Publish the draft:  gh release edit v${NEW_VERSION} --draft=false"
echo ""
