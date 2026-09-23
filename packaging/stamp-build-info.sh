#!/usr/bin/env bash
# =============================================================================
#  stamp-build-info.sh - write core/_build_info.py, the version the running
#  application reports about itself.
#
#  WHY THIS IS A SCRIPT AND NOT THREE COPIES OF A HEREDOC
#    The file is read by services/app_info.py and, through it, by the update
#    check: an application that reports the wrong version compares itself
#    against the latest release and offers an upgrade it already has, forever.
#    That makes the stamp worth exactly one implementation.
#
#  WHY THE ORDER MATTERS MORE THAN THE CONTENT
#    PyInstaller freezes whatever is on disk when it runs. Stamping after it
#    writes a correct file into the repository and ships a stale one inside
#    the bundle, which is what the macOS release job did: the committed value
#    from the previous build travelled into every DMG. Call this BEFORE
#    PyInstaller, always.
#
#  UTC is declared in the string on purpose. It used to be the builder's local
#  time, so the same field meant UTC in CI and Italian time on a laptop, with
#  no way to tell which.
#
#  Usage: bash packaging/stamp-build-info.sh <version>
# =============================================================================
set -euo pipefail

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  echo "usage: $0 <version>      e.g. $0 0.3.1" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${REPO_ROOT}/core/_build_info.py"
BUILD_TS="$(date -u '+%Y-%m-%d %H:%M UTC')"

cat > "$OUT" <<PYEOF
# Generated at build time - do not edit manually.
BUILD_TIME = "${BUILD_TS}"
BUILD_VERSION = "${VERSION}"
PYEOF

echo "Build stamp: v${VERSION} @ ${BUILD_TS}  ->  ${OUT#"${REPO_ROOT}/"}"
