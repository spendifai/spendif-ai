#!/usr/bin/env bash
# =============================================================================
#  release-aliases.sh - publish stable-name copies of the release assets.
#
#  WHY THIS EXISTS
#    The download buttons on the site must be a single click that always lands
#    on the newest installer. GitHub offers exactly one URL shape for that:
#
#      https://github.com/<owner>/<repo>/releases/latest/download/<asset-name>
#
#    It needs the file name to be the SAME in every release, and ours carry the
#    version. So each release gets a second copy of every installer under a
#    fixed name. The site links the fixed names and needs no JavaScript, no API
#    call and no rate limit to resolve them.
#
#  WHY IT RUNS HERE AND NOT IN CI
#    CI publishes a DRAFT with UNSIGNED macOS and Windows packages; the owner
#    signs them locally and replaces them with `gh release upload --clobber`.
#    An alias made in CI would therefore freeze the unsigned build while the
#    versioned name gets the signed one - the same file offered twice, one of
#    them broken, and the broken one is what the site links. Aliases are made
#    from whatever the release holds at the end, after signing.
#
#  WHEN TO RUN IT
#    Step 3bis of the release process: after the signed packages are uploaded
#    and before `gh release edit <tag> --draft=false`.
#
#  Usage: bash packaging/release-aliases.sh v0.3.1
# =============================================================================
set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "usage: $0 <tag>            e.g. $0 v0.3.1" >&2
  exit 2
fi

# Versioned name (glob) -> stable alias. Keep in step with the download links
# in getting-started.html and with the table in the release notes.
PATTERNS=(
  '*-arm64.dmg:SpendifAi-arm64.dmg'
  '*.msix:SpendifAi.msix'
  '*_amd64.deb:spendifai_amd64.deb'
  '*_arm64.deb:spendifai_arm64.deb'
  '*.rpm:spendifai.rpm'
  '*.pkg.tar.zst:spendifai.pkg.tar.zst'
)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> Downloading assets of $TAG"
gh release download "$TAG" --dir "$WORK" --clobber
cd "$WORK"

# A re-run finds the aliases of the previous run sitting in the same folder,
# and the globs below would match them too. Delete them first: an alias must
# never become the source of another alias.
for p in "${PATTERNS[@]}"; do
  rm -f "${p##*:}"
done

MISSING=0
UPLOAD=()
for p in "${PATTERNS[@]}"; do
  glob="${p%%:*}"
  alias_name="${p##*:}"

  # shellcheck disable=SC2086
  matches=( $(ls -1 $glob 2>/dev/null || true) )
  if [ "${#matches[@]}" -eq 0 ]; then
    echo "MISSING: no asset matches '$glob' in $TAG" >&2
    MISSING=1
    continue
  fi
  if [ "${#matches[@]}" -gt 1 ]; then
    echo "AMBIGUOUS: '$glob' matches ${matches[*]} - refusing to guess" >&2
    MISSING=1
    continue
  fi

  src="${matches[0]}"
  cp -f "$src" "$alias_name"
  UPLOAD+=("$alias_name")
  echo "    $src  ->  $alias_name"
done

if [ "$MISSING" -ne 0 ]; then
  echo >&2
  echo "Refusing to upload a partial set of aliases: a name the site links but" >&2
  echo "the release does not carry is a 404 on the download button." >&2
  exit 1
fi

# The checksum file lists the versioned names. Someone who downloads through
# the site gets the alias name instead, and `sha256sum -c` matches on the name:
# without these extra lines the verification they were told to run just fails.
if [ -f SHA256SUMS.txt ]; then
  echo "==> Adding alias lines to SHA256SUMS.txt"
  cp SHA256SUMS.txt SHA256SUMS.orig
  grep -v -F -f <(printf '%s\n' "${UPLOAD[@]}") SHA256SUMS.orig > SHA256SUMS.txt || true
  for alias_name in "${UPLOAD[@]}"; do
    sha256sum "$alias_name" >> SHA256SUMS.txt
  done
  sort -k2 -o SHA256SUMS.txt SHA256SUMS.txt
  UPLOAD+=("SHA256SUMS.txt")
else
  echo "WARNING: no SHA256SUMS.txt in $TAG - aliases will have no published hash" >&2
fi

echo "==> Uploading ${#UPLOAD[@]} files to $TAG"
gh release upload "$TAG" "${UPLOAD[@]}" --clobber

echo "==> Verifying the release now carries every alias"
present="$(gh release view "$TAG" --json assets -q '.assets[].name')"
for p in "${PATTERNS[@]}"; do
  alias_name="${p##*:}"
  if ! grep -qx "$alias_name" <<<"$present"; then
    echo "FAILED: $alias_name is not in the release after upload" >&2
    exit 1
  fi
done

echo
echo "Aliases in place. The site's download buttons will resolve to:"
for p in "${PATTERNS[@]}"; do
  echo "  https://github.com/spendifai/spendif-ai/releases/latest/download/${p##*:}"
done
echo
echo "They only start working once the release stops being a draft:"
echo "  gh release edit $TAG --draft=false"
