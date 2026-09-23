#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — .deb package builder
#  https://github.com/spendifai/spendif-ai
#
#  Produces: build/spendifai_<version>_amd64.deb
#
#  DESIGN CHOICES:
#
#  • WHY a "repo + postinst" .deb instead of a fat PyInstaller bundle?
#    The app has ~40 Python dependencies (pandas, streamlit, llama-cpp, etc.)
#    totalling 500 MB+ when frozen. A repo-style .deb ships only the source
#    code (~5 MB), then postinst runs `uv sync` to install deps into a local
#    venv. This matches how VS Code, Signal, and other desktop apps package
#    for Linux: the .deb is a thin wrapper that bootstraps the real install.
#
#  • WHY /opt/spendifai?
#    FHS 3.0 designates /opt for "add-on application software packages" that
#    are self-contained and don't integrate into /usr. Spendif.ai has its own
#    venv, its own config, and its own data dir — /opt is the correct choice.
#
#  • WHY postinst and not preinst?
#    postinst runs after dpkg has unpacked all files into /opt/spendifai.
#    We need the code present to run `uv sync` (reads pyproject.toml).
#    postinst also creates the .desktop file, downloads the model, and
#    writes the .env — all of which need the code in place.
#
#  • WHY is the C/C++ toolchain only a Recommends?
#    Until 2026-09-22 llama-cpp-python was compiled on the user's machine at
#    first launch, so a compiler was mandatory - and the Depends line listed
#    `gcc, cmake` while the build needs `g++` and `make` too. The RPM spec had
#    the full set; this one did not, and that single missing `g++` is why the
#    package installed cleanly on Ubuntu and then never started: CMake stopped
#    at "Could not find compiler set in environment variable CXX".
#    The package now installs a prebuilt wheel (see [tool.uv.sources] in
#    pyproject.toml), so nothing is compiled on the normal path. The toolchain
#    stays as a Recommends - apt installs it by default, so the optional SSM
#    build keeps working out of the box - but its absence can no longer keep
#    the application from starting.
#
#  • WHY Depends: python3, git, curl (not uv)?
#    uv is not in any distro repo. postinst installs it via the official
#    bootstrap script (curl | sh). Declaring it as a Depends would make
#    the package uninstallable.
#
#  USAGE:
#    cd sw_artifacts
#    bash packaging/linux/build-deb.sh [--version X.Y.Z]
#
#  PREREQUISITES:
#    dpkg-deb (part of dpkg, pre-installed on all Debian/Ubuntu)
#    fakeroot (optional, for correct file ownership without sudo)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Defaults ─────────────────────────────────────────────────────────────────
VERSION=""
ARCH="amd64"

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --arch)    ARCH="$2";    shift 2 ;;
    *)         echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Read version from VERSION file if not specified
if [[ -z "$VERSION" ]]; then
  if [[ -f "${REPO_ROOT}/VERSION" ]]; then
    VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  else
    VERSION="0.0.0"
  fi
fi

# ── Pinned uv ────────────────────────────────────────────────────────────────
# The postinst used to run `curl -LsSf https://astral.sh/uv/install.sh | sh` as
# root: remote code execution at install time, pinned to nothing, and a step no
# Debian archive would accept. We now fetch one named asset and verify it
# against a checksum committed HERE before anything is executed.
#
# The checksum must live in this file, not be downloaded next to the tarball:
# an attacker who can serve you a tampered tarball can serve you its matching
# .sha256 just as easily. A pin is only a pin when it is reviewed in a diff.
#
# To bump: change UV_VERSION, then read the new values from
#   https://github.com/astral-sh/uv/releases/download/<ver>/uv-<triple>.tar.gz.sha256
# and verify them against the tarball you actually downloaded.
# A case, not an associative array: macOS still ships bash 3.2, and this script
# is run by hand on the developer machine as well as by CI.
UV_VERSION="0.12.17"
case "${ARCH}" in
  amd64)
    UV_TRIPLE="x86_64-unknown-linux-gnu"
    UV_SHA256="fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63"
    ;;
  arm64)
    UV_TRIPLE="aarch64-unknown-linux-gnu"
    UV_SHA256="d636d1b678e9e7f367ecb22b46bd1cabbed234d6bc3b4d96365d2b507f72f86c"
    ;;
  *)
    echo "✖ No pinned uv checksum for architecture '${ARCH}'."
    echo "  Add a branch to the case in packaging/linux/build-deb.sh before building."
    exit 1
    ;;
esac

echo "▸ Building spendifai_${VERSION}_${ARCH}.deb"

# ── Build directory ──────────────────────────────────────────────────────────
BUILD_DIR="${REPO_ROOT}/build/deb"
PKG_ROOT="${BUILD_DIR}/spendifai_${VERSION}_${ARCH}"
INSTALL_ROOT="${PKG_ROOT}/opt/spendifai"

rm -rf "${PKG_ROOT}"
mkdir -p "${INSTALL_ROOT}"
mkdir -p "${PKG_ROOT}/DEBIAN"
mkdir -p "${PKG_ROOT}/usr/share/applications"
mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps"

# ── Copy application code ────────────────────────────────────────────────────
echo "▸ Copying application files..."

# Copy only the directories and files needed at runtime
APP_DIRS=(api config core db desktop nsi prompts reports services support ui)
for d in "${APP_DIRS[@]}"; do
  if [[ -d "${REPO_ROOT}/${d}" ]]; then
    cp -r "${REPO_ROOT}/${d}" "${INSTALL_ROOT}/${d}"
  fi
done

# Top-level files
for f in app.py pyproject.toml VERSION .env.example; do
  [[ -f "${REPO_ROOT}/${f}" ]] && cp "${REPO_ROOT}/${f}" "${INSTALL_ROOT}/${f}"
done

# uv.lock for reproducible installs
[[ -f "${REPO_ROOT}/uv.lock" ]] && cp "${REPO_ROOT}/uv.lock" "${INSTALL_ROOT}/uv.lock"

# Icon
ICON_SRC="${REPO_ROOT}/packaging/macos/spendifai_256.png"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps/spendifai.png"
  cp "$ICON_SRC" "${INSTALL_ROOT}/spendifai.png"
fi

# Remove __pycache__ and .pyc
find "${INSTALL_ROOT}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${INSTALL_ROOT}" -name "*.pyc" -delete 2>/dev/null || true

# ── /usr/share/doc: changelog and copyright ─────────────────────────────────
# Both are Debian Policy requirements and lintian errors when missing. The
# version carries no Debian revision, so this is a "native" package and the
# file is changelog.gz, not changelog.Debian.gz.
DOC_DIR="${PKG_ROOT}/usr/share/doc/spendifai"
mkdir -p "${DOC_DIR}"

if [[ -f "${REPO_ROOT}/CHANGELOG.md" ]]; then
  gzip -9 -n -c "${REPO_ROOT}/CHANGELOG.md" > "${DOC_DIR}/changelog.gz"
else
  printf 'spendifai (%s)\n\n  * See https://github.com/spendifai/spendif-ai/releases\n' \
    "${VERSION}" | gzip -9 -n > "${DOC_DIR}/changelog.gz"
fi

# Machine-readable copyright (DEP-5). The licence is not an OSI one, which is
# exactly why it must be stated in the package rather than left to be guessed.
cat > "${DOC_DIR}/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: spendifai
Upstream-Contact: Luigi Corsaro <lcorsaro69@gmail.com>
Source: https://github.com/spendifai/spendif-ai

Files: *
Copyright: 2024-2026 Luigi Corsaro
License: PolyForm-Noncommercial-1.0.0
 Use of this software is permitted for any purpose other than a commercial
 one. The full text is shipped in the source tree as LICENSE and published at
 https://polyformproject.org/licenses/noncommercial/1.0.0/
COPYRIGHT

# ── Stamp build info ────────────────────────────────────────────────────────
# WHY here and not in the repo: the macOS and Windows builders overwrite
# core/_build_info.py in the working tree, which is fine for them because the
# result gets committed at release time. The Linux packages used to ship
# whatever value happened to be committed, so a .deb built from a tag whose
# _build_info.py still held the previous version would report the wrong
# version forever. That was invisible while the number was only decoration;
# now the in-app update check compares against it, and a stale value means a
# permanent false "update available" badge. Stamping into the staged copy gets
# the right version into the package without dirtying the working tree.
cat > "${INSTALL_ROOT}/core/_build_info.py" <<PYEOF
# Generated at build time - do not edit manually.
BUILD_TIME = "$(date -u '+%Y-%m-%d %H:%M UTC')"
BUILD_VERSION = "${VERSION}"
PYEOF

echo "✔ Application files copied"

# ── DEBIAN/control ───────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: spendifai
Version: ${VERSION}
Section: misc
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.12), python3-venv, python3-dev, python3-gi, python3-cairo, gir1.2-webkit2-4.1, git, curl, pkgconf, zenity
Recommends: g++, gcc, make, cmake
Installed-Size: $(du -sk "${INSTALL_ROOT}" | cut -f1)
Maintainer: Luigi Corsaro <lcorsaro69@gmail.com>
Homepage: https://github.com/spendifai/spendif-ai
Description: Personal finance manager with local AI categorisation
 Spendif.ai aggregates bank statements (CSV/XLSX) into a unified ledger
 with automatic categorisation via local LLM (llama.cpp). Features include
 card-account reconciliation, internal transfer detection, budget tracking,
 and interactive analytics. Runs fully offline with no cloud dependency.
EOF

# ── DEBIAN/postinst ──────────────────────────────────────────────────────────
# Postinst runs as ROOT, with $HOME=/root. Anything user-specific (venv,
# model download, ~/.spendifai) belongs in a script that runs at FIRST
# USER LAUNCH instead — postinst here only installs uv system-wide so
# every desktop user can use it. The .desktop Exec line spawns the
# launch.sh wrapper which performs the per-user setup the first time.
{
# The pinned values are the only part that varies per build, so they are
# written as a prelude and the rest stays a quoted heredoc: no escaping, and
# what you read below is exactly what ships.
cat <<PRELUDE
#!/bin/bash
# =============================================================================
#  Spendif.ai - post-installation (root context, minimal)
#  Only system-wide setup. Per-user setup runs at first launch via launch.sh.
# =============================================================================
set -e

UV_VERSION="${UV_VERSION}"
UV_TRIPLE="${UV_TRIPLE}"
UV_SHA256="${UV_SHA256}"
PRELUDE
cat <<'POSTINST'

echo ""
echo "  Spendif.ai — post-install"
echo ""

# ── 1. System-wide uv install ───────────────────────────────────────────────
# Place uv in /usr/local/bin so EVERY user (not just root) has it on PATH.
#
# One named asset, one checksum verified BEFORE anything runs. Nothing is
# piped into a shell: this runs as root, and a compromised or merely changed
# upstream install script would own the machine.
if ! [ -x /usr/local/bin/uv ]; then
  echo "  > Installing uv ${UV_VERSION} to /usr/local/bin..."
  TMP_UV_DIR=$(mktemp -d)
  UV_TARBALL="$TMP_UV_DIR/uv.tar.gz"
  UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TRIPLE}.tar.gz"

  if curl -fsSL -o "$UV_TARBALL" "$UV_URL"; then
    ACTUAL=$(sha256sum "$UV_TARBALL" | awk '{print $1}')
    if [ "$ACTUAL" = "$UV_SHA256" ]; then
      tar -xzf "$UV_TARBALL" -C "$TMP_UV_DIR"
      if install -m 0755 "$TMP_UV_DIR/uv-${UV_TRIPLE}/uv" /usr/local/bin/uv; then
        install -m 0755 "$TMP_UV_DIR/uv-${UV_TRIPLE}/uvx" /usr/local/bin/uvx 2>/dev/null || true
      fi
    else
      # Refuse, loudly, and do not fall back to anything. A mismatch is either
      # a corrupted download or a substituted artefact, and we cannot tell
      # which. launch.sh installs uv per-user on first launch, so the user is
      # not stranded.
      echo "  !! uv checksum mismatch - refusing to install it."
      echo "     expected $UV_SHA256"
      echo "     got      $ACTUAL"
    fi
  else
    echo "  !! Could not download uv from $UV_URL"
  fi

  rm -rf "$TMP_UV_DIR"
fi
if [ -x /usr/local/bin/uv ]; then
  echo "  ✔ uv: $(/usr/local/bin/uv --version 2>&1 | head -1)"
else
  echo "  ⚠ uv install failed — user will be prompted to install on first launch."
fi

# ── 1b. Record how this copy was installed ──────────────────────────────────
# postinst runs as root and every user on the machine shares this install, so
# the marker goes next to the code, not in a home directory. launch.sh mirrors
# it into the launching user's ~/.spendifai. See services/update_service.py.
echo "deb" > /opt/spendifai/.install_method || true

# ── 2. Refresh icon + desktop caches ────────────────────────────────────────
if command -v gtk-update-icon-cache &>/dev/null; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
fi
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database /usr/share/applications 2>/dev/null || true
fi

echo ""
echo "  ✔ Spendif.ai installed."
echo "    On first launch the app will set up a per-user Python venv in"
echo "    ~/.spendifai/.venv and download the recommended AI model (~3 GB)."
echo "    Launch: search 'Spendif' in Activities, or run /opt/spendifai/launch.sh"
echo ""
POSTINST
} > "${PKG_ROOT}/DEBIAN/postinst"

chmod 0755 "${PKG_ROOT}/DEBIAN/postinst"

# ── DEBIAN/prerm ─────────────────────────────────────────────────────────────
# Per-user venv lives in ~/.spendifai/.venv (created by launch.sh on first
# run), not under /opt — so there is nothing for prerm to clean other than
# remnants of the old layout (≤ 0.1.0 used /opt/spendifai/.venv).
cat > "${PKG_ROOT}/DEBIAN/prerm" <<'PRERM'
#!/bin/bash
set -e
# Legacy layout cleanup (pre-launch.sh installs put the venv under /opt)
rm -rf /opt/spendifai/.venv 2>/dev/null || true
echo "  Spendif.ai removed."
echo "  Per-user data preserved in ~/.spendifai/ — wipe with:"
echo "    bash /opt/spendifai/cleanup.sh   (if available)  OR  rm -rf ~/.spendifai"
PRERM

chmod 0755 "${PKG_ROOT}/DEBIAN/prerm"

# ── /opt/spendifai/launch.sh — per-user first-launch + run wrapper ──────────
# Single source of truth in packaging/linux/launch.sh; .deb and .rpm both
# copy it. Edit the script there, never inline here.
cp "${SCRIPT_DIR}/launch.sh" "${INSTALL_ROOT}/launch.sh"
chmod 0755 "${INSTALL_ROOT}/launch.sh"


# ── .desktop file ────────────────────────────────────────────────────────────
cat > "${PKG_ROOT}/usr/share/applications/spendifai.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Spendif.ai
Comment=Personal finance manager with local AI categorisation
Exec=/opt/spendifai/launch.sh
Icon=spendifai
Terminal=false
Categories=Office;Finance;
StartupNotify=true
StartupWMClass=spendifai
Keywords=finance;budget;bank;expense;
DESKTOP

# ── Set permissions ──────────────────────────────────────────────────────────
# /opt/spendifai is read-only source code; per-user venv lives in
# ~/.spendifai/.venv (created by launch.sh on first run).
find "${INSTALL_ROOT}" -type f -exec chmod 644 {} +
find "${INSTALL_ROOT}" -type d -exec chmod 755 {} +
# launch.sh MUST be executable — it's the .desktop file's Exec target.
# (The generic 0644 find above clobbers the chmod inside the heredoc.)
chmod 0755 "${INSTALL_ROOT}/launch.sh"
chmod 644 "${PKG_ROOT}/usr/share/applications/spendifai.desktop"

# ── Build .deb ───────────────────────────────────────────────────────────────
DEB_PATH="${REPO_ROOT}/build/spendifai_${VERSION}_${ARCH}.deb"

echo "▸ Building .deb package..."
if command -v fakeroot &>/dev/null; then
  fakeroot dpkg-deb --build "${PKG_ROOT}" "${DEB_PATH}"
else
  dpkg-deb --build "${PKG_ROOT}" "${DEB_PATH}"
fi

DEB_SIZE=$(du -h "${DEB_PATH}" | cut -f1)
echo "✔ Package built: ${DEB_PATH} (${DEB_SIZE})"
echo ""
echo "  Install:   sudo dpkg -i ${DEB_PATH}"
echo "  Or:        sudo apt install ./${DEB_PATH}"
echo "  Uninstall: sudo apt remove spendifai"
echo ""

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -rf "${PKG_ROOT}"
echo "✔ Build directory cleaned up"
