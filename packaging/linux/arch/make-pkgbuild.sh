#!/usr/bin/env bash
# =============================================================================
#  make-pkgbuild.sh - derive a recipe that packages something other than a
#  published release, without keeping a second copy of the recipe around.
#
#  THE PROBLEM
#    The committed PKGBUILD points at a published release tarball and pins its
#    sha256. That is exactly right for the AUR, where the pinned hash is the
#    only integrity anchor a user has before compiling, and wrong for the two
#    cases where the release does not exist yet:
#
#      --branch    test a fix before it is released. Building the committed
#                  recipe would package the OLD code while looking like a
#                  successful test, which is the opposite of a test.
#
#      --release   build the package IN CI for the tag being released. The
#                  source is the checkout itself, so the package is derived
#                  from the exact commit and nothing is downloaded: no
#                  checksum to bump by hand at release time, and no race with
#                  GitHub generating the tag tarball.
#
#  WHY DERIVE INSTEAD OF COMMITTING VARIANTS
#    A copy goes stale the first time the real recipe changes, silently,
#    because nothing compares the two. This reads the real recipe every run.
#
#  WHAT MUST NEVER BE PUBLISHED
#    The --branch output: a branch tarball is regenerated whenever the branch
#    moves, so its checksum is SKIP. Only the committed recipe goes to the AUR.
#
#  USAGE
#    bash make-pkgbuild.sh --branch fix/something
#    bash make-pkgbuild.sh --release 0.3.1 --tarball /path/spendifai-0.3.1.tar.gz
#    (both write into ./build next to this script unless --out says otherwise)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_PKGBUILD="${SCRIPT_DIR}/PKGBUILD"
OUT_DIR="${SCRIPT_DIR}/build"

MODE=""
BRANCH=""
VERSION=""
TARBALL=""

usage() {
  sed -n '2,33p' "$0" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --branch)  MODE="branch";  BRANCH="${2:-}"; shift 2 ;;
    --release) MODE="release"; VERSION="${2:-}"; shift 2 ;;
    --tarball) TARBALL="${2:-}"; shift 2 ;;
    --out)     OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -r "$SRC_PKGBUILD" ] || { echo "No PKGBUILD next to this script: $SRC_PKGBUILD" >&2; exit 1; }

mkdir -p "$OUT_DIR"
cp -f "${SCRIPT_DIR}/spendifai.install" "$OUT_DIR/"

case "$MODE" in
  branch)
    [ -n "$BRANCH" ] || { echo "--branch needs a branch name" >&2; exit 2; }

    # GitHub names the directory inside a branch archive after the repository
    # and the branch, with every slash turned into a dash:
    # fix/installer-reliability -> spendif-ai-fix-installer-reliability.
    # package() cd's into it, so that line has to move with the source.
    SRC_DIR_NAME="spendif-ai-${BRANCH//\//-}"

    awk -v branch="$BRANCH" -v srcdir="$SRC_DIR_NAME" '
      /^source=\(/ {
        print "# TEST BUILD - branch tarball, not a release. Never publish this file."
        print "source=(\"${pkgname}-${pkgver}.tar.gz::${url}/archive/refs/heads/" branch ".tar.gz\")"
        next
      }
      /^sha256sums=\(/ {
        print "# SKIP because a branch tarball is regenerated whenever the branch moves."
        print "sha256sums=(\x27SKIP\x27)"
        next
      }
      /cd "\$\{srcdir\}\/spendif-ai-\$\{pkgver\}"/ {
        print "  cd \"${srcdir}/" srcdir "\""
        next
      }
      { print }
    ' "$SRC_PKGBUILD" > "${OUT_DIR}/PKGBUILD"

    CHECKS=("archive/refs/heads/${BRANCH}.tar.gz" "'SKIP'" "${SRC_DIR_NAME}")
    SUMMARY="branch ${BRANCH} (checksum SKIP - test only, never publish)"
    ;;

  release)
    [ -n "$VERSION" ] || { echo "--release needs a version" >&2; exit 2; }
    [ -n "$TARBALL" ] && [ -r "$TARBALL" ] || { echo "--tarball needs a readable file" >&2; exit 2; }

    # The tarball must unpack into spendif-ai-<version>/, which is what
    # package() enters and what `git archive --prefix=` produces. Naming the
    # file <pkgname>-<pkgver>.tar.gz lets the source line stay a plain local
    # reference, so makepkg never touches the network.
    LOCAL_NAME="spendifai-${VERSION}.tar.gz"
    cp -f "$TARBALL" "${OUT_DIR}/${LOCAL_NAME}"
    SUM="$(sha256sum "${OUT_DIR}/${LOCAL_NAME}" | awk '{print $1}')"

    awk -v ver="$VERSION" -v sum="$SUM" '
      /^pkgver=/ { print "pkgver=" ver; next }
      /^source=\(/ {
        print "# Built from the checkout of the tag being released: the bytes are"
        print "# the commit, not a tarball fetched back from the forge."
        print "source=(\"${pkgname}-${pkgver}.tar.gz\")"
        next
      }
      /^sha256sums=\(/ { print "sha256sums=(\x27" sum "\x27)"; next }
      { print }
    ' "$SRC_PKGBUILD" > "${OUT_DIR}/PKGBUILD"

    CHECKS=("pkgver=${VERSION}" "${SUM}")
    SUMMARY="release ${VERSION} from ${LOCAL_NAME} (sha256 ${SUM:0:12}...)"
    ;;

  *)
    echo "Pick a mode: --branch <name> or --release <version> --tarball <file>" >&2
    usage
    ;;
esac

# A derived recipe that quietly kept pointing at the published release would
# build the wrong code and still pass, so the rewrite is verified rather than
# assumed.
for needle in "${CHECKS[@]}"; do
  grep -qF "$needle" "${OUT_DIR}/PKGBUILD" || {
    echo "Rewrite failed: '${needle}' is not in the generated PKGBUILD." >&2
    echo "The committed recipe has probably changed shape; fix this script." >&2
    exit 1
  }
done

bash -n "${OUT_DIR}/PKGBUILD"

echo "Recipe written to ${OUT_DIR}/PKGBUILD"
echo "  ${SUMMARY}"
echo
echo "Next:  cd ${OUT_DIR} && makepkg -f -d"
[ "$MODE" = "branch" ] && cat <<'EOF'

The package will carry the version from the committed recipe while containing
branch code. That is fine for a test and wrong for anything else: remove it
when you are done, with  sudo pacman -Rns spendifai
EOF
exit 0
