#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — .deb package builder
#  https://github.com/spendifai/spendif-ai
#
#  Produces: build/spendifai_<version>_amd64.deb
#
#  DESIGN CHOICES:
#
#  • WHY a self-contained bundle now, and not source plus a first-launch sync?
#    It used to ship the source and build an environment on first launch
#    against the distribution's Python. That handed the product to the
#    distribution: Arch ships 3.14 and Debian 12 ships 3.11, and on
#    2026-09-23 we were outside the supported range on both sides on the same
#    day. The bundle replaces a moving target with one number we choose, the
#    glibc of the machine that built it, which is compatible forwards: built
#    on 22.04 it reaches Debian 12, Ubuntu 22.04 and Mint 21 and everything
#    newer, and where it does not reach it fails loudly.
#
#    The price is stated rather than hidden: the package goes from about
#    600 KB to a few hundred megabytes, and the ability to swap in a
#    different inference wheel at first launch is gone, so what the package
#    carries is what it runs.
#
#  • WHY /opt/spendifai?
#    FHS 3.0 designates /opt for add-on application software packages that
#    are self-contained and do not integrate into /usr. This one is exactly
#    that: its own Python, its own dependencies, its own data directory.
#
#  • WHY almost no Depends?
#    Because the bundle carries what it needs. The Python interpreter, the
#    toolchain, the GTK stack and the tooling that installed dependencies at
#    first launch are all gone from the dependency line, and with them the
#    class of failure where a package installs cleanly and then cannot start.
#    libvulkan1 is a Recommends and not a Depends: without it the application
#    runs on the processor, which is slower and not broken.
#
#  • WHY no native window?
#    PyGObject publishes no wheels and pycairo publishes them for Windows
#    only, so a bundled Python cannot use the distribution's python3-gi,
#    which is built for the ABI of the distribution's own Python - the exact
#    dependency this package exists to remove. The interface opens in the
#    default browser instead, which works everywhere by construction.
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
    --bundle-dir)  BUNDLE_DIR="$2"; shift 2 ;;
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

# ── Copy the bundle ──────────────────────────────────────────────────────────
# What ships is a self-contained bundle: its own Python, its own dependencies,
# its own inference library with the accelerator plugins beside it.
#
# It used to ship the source and build an environment on first launch against
# the distribution's Python. That handed the product to the distribution: Arch
# ships 3.14 and Debian 12 ships 3.11, and on one day in September we were out
# of range on both sides at once. The bundle replaces that moving target with
# one number we choose, the glibc of the machine that built it.
echo "▸ Copying the bundle..."

if [[ -z "${BUNDLE_DIR:-}" ]]; then
  BUNDLE_DIR="${REPO_ROOT}/dist/SpendifAi"
fi

if [[ ! -d "${BUNDLE_DIR}" ]]; then
  echo "✖ No bundle at ${BUNDLE_DIR}."
  echo "  Build one with:"
  echo "    uv run --no-sync --extra desktop pyinstaller desktop.spec --noconfirm --clean"
  echo "  or point at one with:  --bundle-dir <path>"
  exit 1
fi

cp -a "${BUNDLE_DIR}/." "${INSTALL_ROOT}/"

# The accelerators are plugins that nothing links to, so any tool copying this
# payload can drop them without noticing, and the application then runs on the
# processor and says nothing about it. This is one of those copies, so it is
# checked here too.
for lib in libggml-base.so libggml-cpu.so; do
  find "${INSTALL_ROOT}" -name "${lib}" | grep -q . || {
    echo "✖ ${lib} is not in the payload: the bundle is incomplete."
    exit 1
  }
done
if ! find "${INSTALL_ROOT}" -name "libggml-vulkan.so" | grep -q .; then
  echo "⚠ No Vulkan backend in the payload: this package will run on the processor only."
fi

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
Depends:
Recommends: libvulkan1
Suggests: mesa-vulkan-drivers
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
# Runs as root, with HOME=/root, so nothing user-specific belongs here. It
# used to install a pinned uv system-wide and leave the rest to a sync at
# first launch; the bundle carries its own Python and dependencies, so what
# remains is desktop integration and a marker recording how this copy was
# installed.
{
cat <<'POSTINST'
#!/bin/bash
# =============================================================================
#  Spendif.ai - post-installation (root context, minimal)
# =============================================================================
set -e

echo ""
echo "  Spendif.ai — post-install"
echo ""

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
# Directories readable, data files readable, and everything that has to run
# left alone. A blanket 644 over a bundle strips the executable bit from the
# launcher and from every shared library, and the package then installs
# perfectly and cannot start.
find "${INSTALL_ROOT}" -type d -exec chmod 755 {} +
find "${INSTALL_ROOT}" -type f ! -perm -u+x ! -name "*.so*" -exec chmod 644 {} +
find "${INSTALL_ROOT}" -type f \( -perm -u+x -o -name "*.so*" \) -exec chmod 755 {} +
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
