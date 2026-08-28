#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — .rpm package builder
#  https://github.com/spendifai/spendif-ai
#
#  Produces: build/spendifai-<version>-1.<arch>.rpm
#
#  DESIGN CHOICES:
#
#  • Same "repo + launch.sh" approach as the .deb builder. The RPM ships
#    source code to /opt/spendifai (read-only) plus packaging/linux/launch.sh
#    as /opt/spendifai/launch.sh. The .desktop file points Exec= at that
#    wrapper. First user launch creates ~/.spendifai/.venv via `uv sync`
#    against the system Python (PyGObject + cairo via --system-site-packages).
#    %post does NOT touch user state — it only installs uv to /usr/local/bin
#    and refreshes icon/desktop caches.
#
#  • WHY rpmbuild (not fpm)?
#    rpmbuild is the native RPM build tool, pre-installed on all Red Hat
#    systems. fpm is convenient but adds a Ruby dependency. We use rpmbuild
#    with a minimal .spec generated inline.
#
#  • Fedora package names: python3-gobject (not python3-gi), python3-cairo,
#    webkit2gtk4.1 (Fedora ≥ 37), zenity, gtk3.
#
#  USAGE:
#    cd sw_artifacts
#    bash packaging/linux/build-rpm.sh [--version X.Y.Z] [--arch x86_64|aarch64]
#
#  PREREQUISITES:
#    rpm-build (sudo dnf install rpm-build, or sudo apt install rpm)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Defaults ─────────────────────────────────────────────────────────────────
VERSION=""
ARCH="x86_64"
RELEASE="1"

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --arch)    ARCH="$2";    shift 2 ;;
    *)         echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  if [[ -f "${REPO_ROOT}/VERSION" ]]; then
    VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  else
    VERSION="0.0.0"
  fi
fi

echo "▸ Building spendifai-${VERSION}-${RELEASE}.${ARCH}.rpm"

# ── Check rpmbuild ───────────────────────────────────────────────────────────
if ! command -v rpmbuild &>/dev/null; then
  echo "✖ rpmbuild not found. Install it:"
  echo "  Fedora/RHEL: sudo dnf install rpm-build"
  echo "  Ubuntu/Debian (cross-build): sudo apt install rpm"
  exit 1
fi

# ── Build directory ──────────────────────────────────────────────────────────
BUILD_DIR="${REPO_ROOT}/build/rpm"
RPM_TOPDIR="${BUILD_DIR}/rpmbuild"

rm -rf "${RPM_TOPDIR}"
mkdir -p "${RPM_TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# ── Create tarball (source archive) ──────────────────────────────────────────
echo "▸ Creating source tarball..."
TARBALL_NAME="spendifai-${VERSION}"
TARBALL_DIR="${BUILD_DIR}/${TARBALL_NAME}"
rm -rf "${TARBALL_DIR}"
mkdir -p "${TARBALL_DIR}"

# Application directories
APP_DIRS=(api config core db desktop nsi prompts reports services support ui)
for d in "${APP_DIRS[@]}"; do
  [[ -d "${REPO_ROOT}/${d}" ]] && cp -r "${REPO_ROOT}/${d}" "${TARBALL_DIR}/${d}"
done

# Top-level files
for f in app.py pyproject.toml VERSION .env.example; do
  [[ -f "${REPO_ROOT}/${f}" ]] && cp "${REPO_ROOT}/${f}" "${TARBALL_DIR}/${f}"
done
[[ -f "${REPO_ROOT}/uv.lock" ]] && cp "${REPO_ROOT}/uv.lock" "${TARBALL_DIR}/uv.lock"

# Single source of truth for the user-space launcher (shared with .deb)
cp "${SCRIPT_DIR}/launch.sh" "${TARBALL_DIR}/launch.sh"
chmod 0755 "${TARBALL_DIR}/launch.sh"

# Icon — record whether we are shipping one so the spec %files section
# can list (or omit) the icon path. rpmbuild requires every %files entry
# to exist on disk; an unconditional icon line is a hard build failure
# when the source PNG is missing (closes AI-56).
ICON_SRC="${REPO_ROOT}/packaging/macos/spendifai_256.png"
ICON_PRESENT=0
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "${TARBALL_DIR}/spendifai.png"
  ICON_PRESENT=1
fi

# Clean
find "${TARBALL_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${TARBALL_DIR}" -name "*.pyc" -delete 2>/dev/null || true

# Create tarball
tar -czf "${RPM_TOPDIR}/SOURCES/${TARBALL_NAME}.tar.gz" -C "${BUILD_DIR}" "${TARBALL_NAME}"
rm -rf "${TARBALL_DIR}"
echo "✔ Source tarball created"

# ── RPM .spec file ───────────────────────────────────────────────────────────
cat > "${RPM_TOPDIR}/SPECS/spendifai.spec" <<SPEC
Name:           spendifai
Version:        ${VERSION}
Release:        ${RELEASE}%{?dist}
Summary:        Personal finance manager with local AI categorisation
License:        MIT
URL:            https://github.com/spendifai/spendif-ai
Source0:        %{name}-%{version}.tar.gz

# Runtime dependencies (Fedora ≥ 37 / RHEL 9 names)
Requires:       python3 >= 3.12
Requires:       python3-devel
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       webkit2gtk4.1
Requires:       gtk3
Requires:       zenity
Requires:       git
Requires:       curl
Requires:       gcc
Requires:       gcc-c++
Requires:       make
Requires:       cmake
Requires:       pkgconfig

# Build is just unpacking — no compilation needed
BuildArch:      noarch

%description
Spendif.ai aggregates heterogeneous bank statements (CSV/XLSX) into a unified
chronological ledger with automatic categorisation via local LLM (llama.cpp).
Features include card-account reconciliation, internal transfer detection,
budget tracking, and interactive analytics. Runs fully offline.

The package ships source code to /opt/spendifai. On first user launch the
included launch.sh wrapper creates a per-user venv in ~/.spendifai/.venv
(using the system Python with --system-site-packages so PyGObject and cairo
are available) and runs uv sync against the shipped uv.lock.

%prep
%setup -q

%install
mkdir -p %{buildroot}/opt/spendifai
cp -r * %{buildroot}/opt/spendifai/

# launch.sh must remain executable through the install
chmod 0755 %{buildroot}/opt/spendifai/launch.sh

# .desktop file — Exec points at launch.sh, the per-user wrapper. Runs as
# the logged-in user (gnome-shell/kde-plasma spawn it), not root.
mkdir -p %{buildroot}/usr/share/applications
cat > %{buildroot}/usr/share/applications/spendifai.desktop <<'DESKTOP'
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

# Icon
mkdir -p %{buildroot}/usr/share/icons/hicolor/256x256/apps
if [ -f spendifai.png ]; then
  cp spendifai.png %{buildroot}/usr/share/icons/hicolor/256x256/apps/spendifai.png
fi

%post
# Post-install runs as ROOT with \$HOME=/root. Anything user-specific (venv,
# model download, ~/.spendifai/.env) belongs in launch.sh, which runs at
# first user launch. Here we only install uv system-wide and refresh caches.

echo ""
echo "  Spendif.ai — post-install"
echo ""

# ── System-wide uv install ──────────────────────────────────────────────────
# Place uv in /usr/local/bin so EVERY user (not just root) has it on PATH.
if ! [ -x /usr/local/bin/uv ]; then
  echo "  ▸ Installing uv to /usr/local/bin..."
  TMP_UV_DIR=\$(mktemp -d)
  curl -LsSf https://astral.sh/uv/install.sh | \\
    env XDG_CONFIG_HOME=/tmp UV_INSTALL_DIR=/usr/local/bin sh -s -- --no-modify-path 2>&1 | tail -3
  # Fallback if UV_INSTALL_DIR was ignored by an older bootstrap script:
  if ! [ -x /usr/local/bin/uv ] && [ -x /root/.local/bin/uv ]; then
    cp /root/.local/bin/uv /usr/local/bin/uv
    chmod 0755 /usr/local/bin/uv
  fi
  rm -rf "\$TMP_UV_DIR"
fi
if [ -x /usr/local/bin/uv ]; then
  echo "  ✔ uv: \$(/usr/local/bin/uv --version 2>&1 | head -1)"
else
  echo "  ⚠ uv install failed — launch.sh will retry per-user on first launch."
fi

# ── Refresh icon + desktop caches ───────────────────────────────────────────
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications 2>/dev/null || true

echo ""
echo "  ✔ Spendif.ai installed."
echo "    On first launch the app will create a per-user Python venv in"
echo "    ~/.spendifai/.venv and download the recommended AI model (~1-3 GB)."
echo "    Launch: search 'Spendif' in Activities, or run /opt/spendifai/launch.sh"
echo ""

%preun
# Per-user venv lives in ~/.spendifai/.venv (created by launch.sh). Only
# clean up legacy ≤0.1.0 layouts that stored the venv under /opt.
rm -rf /opt/spendifai/.venv 2>/dev/null || true
echo "  Spendif.ai removed. Per-user data preserved in ~/.spendifai/."
echo "  Wipe with: bash /opt/spendifai/cleanup.sh  (if available)  OR  rm -rf ~/.spendifai"

%files
%defattr(-,root,root,-)
/opt/spendifai/
/usr/share/applications/spendifai.desktop
$([ "$ICON_PRESENT" = "1" ] && echo "/usr/share/icons/hicolor/256x256/apps/spendifai.png")

%changelog
* $(date '+%a %b %d %Y') Luigi Corsaro <lcorsaro69@gmail.com> - ${VERSION}-${RELEASE}
- Use shared launch.sh wrapper for per-user venv setup (same as .deb)
- %post no longer runs uv sync or downloads model — moved to first launch
- Add zenity / python3-cairo / webkit2gtk4.1 / gtk3 deps for pywebview GTK
SPEC

# ── Build RPM ────────────────────────────────────────────────────────────────
# Cross-build awareness: when invoked on macOS via Homebrew's rpm,
# rpmbuild stamps Os: darwin in the package header. Fedora's dnf then
# refuses with "intended for a different operating system". Force the
# target OS to linux. On native Linux this is a no-op.
echo "▸ Running rpmbuild..."
rpmbuild \
  --target "noarch-linux" \
  --define "_topdir ${RPM_TOPDIR}" \
  --define "_target_os linux" \
  --define "_host_os linux" \
  -bb "${RPM_TOPDIR}/SPECS/spendifai.spec"

# ── Move output ──────────────────────────────────────────────────────────────
RPM_OUTPUT=$(find "${RPM_TOPDIR}/RPMS" -name "*.rpm" -type f | head -1)
if [[ -n "$RPM_OUTPUT" ]]; then
  FINAL_RPM="${REPO_ROOT}/build/$(basename "$RPM_OUTPUT")"
  mv "$RPM_OUTPUT" "$FINAL_RPM"
  RPM_SIZE=$(du -h "$FINAL_RPM" | cut -f1)
  echo ""
  echo "✔ Package built: ${FINAL_RPM} (${RPM_SIZE})"
  echo ""
  echo "  Install:   sudo dnf install ${FINAL_RPM}"
  echo "  Or:        sudo rpm -i ${FINAL_RPM}"
  echo "  Uninstall: sudo dnf remove spendifai"
  echo ""
else
  echo "✖ rpmbuild did not produce an RPM. Check output above."
  exit 1
fi

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -rf "${RPM_TOPDIR}"
echo "✔ Build directory cleaned up"
