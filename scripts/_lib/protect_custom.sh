# shellcheck shell=bash
# protect_custom.sh — shared logic to protect custom-compiled packages from `uv sync`.
#
# Sourced by:
#   - scripts/safe_sync.sh        (interactive: prompts user before overwriting custom builds)
#   - start.sh                    (non-interactive: skips sync if custom builds would be touched)
#   - benchmark/run_benchmark_full.sh (interactive)
#
# Required caller-set vars:
#   PYTHON           Python binary (e.g. ".venv/bin/python" or "python3").
#
# Optional caller-set vars (with defaults):
#   SAFE_SYNC_MODE   "interactive" (default) or "non-interactive".
#                    interactive   → prompt user (y/N) before sync that would touch custom packages.
#                    non-interactive → skip sync entirely if custom packages would be touched.
#   _CUSTOM_LIST     Path to .custom_packages file (default: "benchmark/.custom_packages").
#
#  TWO FILES, ONE QUESTION. The list above is a declaration that belongs to a
#  development checkout. The same question is asked by the packaged application,
#  which has no checkout to read: /opt is read-only and `benchmark/` is not even
#  shipped. So whoever installs a custom build also records it INSIDE the venv,
#  in `<venv>/.custom_packages`, and both files are read here. The venv is the
#  thing that actually knows what is inside it; the repository file only says
#  what this checkout expects to protect.
#
# Public API: `safe_sync_run` — runs `uv sync --inexact --quiet` with protection.

SAFE_SYNC_MODE="${SAFE_SYNC_MODE:-interactive}"
PYTHON="${PYTHON:-python3}"
_CUSTOM_LIST="${_CUSTOM_LIST:-benchmark/.custom_packages}"
_CUSTOM_BACKUP=".venv/_custom_backup"
_SITE_PKGS=""

# Every file that answers "which packages here are custom builds". Callers may
# point VENV_DIR elsewhere; by default the venv sits next to the checkout.
_safe_sync_list_files() {
    local f
    for f in "$_CUSTOM_LIST" "${VENV_DIR:-.venv}/.custom_packages"; do
        [ -f "$f" ] && echo "$f"
    done
}

_safe_sync_init_paths() {
    # IMPORTANTE: il backup/restore deve operare sul site-packages del VENV
    # (lì vivono i pacchetti gestiti da `uv sync`), non sul Python di sistema.
    # Su first-run il venv non esiste ancora: fallback a $PYTHON (sistema)
    # ma in quel caso non c'è nulla di custom da proteggere comunque.
    local py
    if [ -x ".venv/bin/python" ]; then
        py=".venv/bin/python"
    elif [ -x ".venv/bin/python3" ]; then
        py=".venv/bin/python3"
    else
        py="$PYTHON"
    fi
    _SITE_PKGS=$("$py" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || echo "")
}

# Extract version from dist-info dir name: "llama_cpp_python-0.3.19.dist-info" → "0.3.19"
_safe_sync_pkg_version() {
    local pkg_under="$1"
    local search_dir="${2:-$_SITE_PKGS}"
    local di
    di=$(find "${search_dir}" -maxdepth 1 -name "${pkg_under}-*.dist-info" -type d 2>/dev/null | head -1)
    [ -z "$di" ] && echo "" && return
    basename "$di" | sed "s/^${pkg_under}-//" | sed 's/\.dist-info$//'
}

# Compare versions: returns 0 if v1 >= v2, 1 if v1 < v2
_safe_sync_version_ge() {
    [ "$1" = "$2" ] && return 0
    local sorted_first
    sorted_first=$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)
    [ "$sorted_first" = "$2" ]
}

_safe_sync_backup_custom() {
    [ -z "$_SITE_PKGS" ] && return
    [ -n "$(_safe_sync_list_files)" ] || return
    rm -rf "$_CUSTOM_BACKUP"
    mkdir -p "$_CUSTOM_BACKUP"
    local n=0
    while IFS= read -r pkg || [ -n "$pkg" ]; do
        [ -z "$pkg" ] && continue
        [[ "$pkg" == \#* ]] && continue
        local pkg_under
        pkg_under=$(echo "$pkg" | tr '-' '_')
        local ver
        ver=$(_safe_sync_pkg_version "$pkg_under")
        [ -z "$ver" ] && continue  # not installed, nothing to protect
        for d in "$_SITE_PKGS/${pkg_under}" \
                 "$_SITE_PKGS/${pkg_under}-${ver}.dist-info" \
                 "$_SITE_PKGS/${pkg_under}.libs"; do
            [ -e "$d" ] && cp -a "$d" "$_CUSTOM_BACKUP/"
        done
        echo "$pkg_under=$ver" >> "$_CUSTOM_BACKUP/_versions"
        n=$((n + 1))
    done < <(cat $(_safe_sync_list_files) 2>/dev/null)
    [ "$n" -gt 0 ] && echo "[safe_sync] Backed up $n custom package(s)"
    return 0
}

_safe_sync_restore_if_downgraded() {
    [ -f "$_CUSTOM_BACKUP/_versions" ] || return
    local restored=0 kept=0
    while IFS='=' read -r pkg_under old_ver || [ -n "$pkg_under" ]; do
        [ -z "$pkg_under" ] && continue
        [ -z "$old_ver" ] && continue
        local new_ver
        new_ver=$(_safe_sync_pkg_version "$pkg_under" "$_SITE_PKGS")
        local action=""
        if [ -z "$new_ver" ]; then
            action="removed → restore"
        elif _safe_sync_version_ge "$new_ver" "$old_ver"; then
            action="upgraded ${old_ver}→${new_ver} → keep"
            kept=$((kept + 1))
            continue
        else
            action="downgraded ${old_ver}→${new_ver} → restore"
        fi
        echo "[safe_sync] $pkg_under: $action"
        rm -rf "${_SITE_PKGS:?}/${pkg_under}" \
               "${_SITE_PKGS:?}/${pkg_under}-"*.dist-info \
               "${_SITE_PKGS:?}/${pkg_under}.libs"
        for d in "$_CUSTOM_BACKUP/${pkg_under}" \
                 "$_CUSTOM_BACKUP/${pkg_under}-${old_ver}.dist-info" \
                 "$_CUSTOM_BACKUP/${pkg_under}.libs"; do
            [ -e "$d" ] && cp -a "$d" "$_SITE_PKGS/"
        done
        restored=$((restored + 1))
    done < "$_CUSTOM_BACKUP/_versions"
    rm -rf "$_CUSTOM_BACKUP"
    [ "$restored" -gt 0 ] && echo "[safe_sync] Restored $restored custom package(s)"
    [ "$kept" -gt 0 ] && echo "[safe_sync] Kept $kept uv-upgraded package(s)"
    return 0
}

_safe_sync_would_touch_custom() {
    [ -f "$_CUSTOM_BACKUP/_versions" ] || { echo "false"; return; }
    local dry_output
    dry_output=$(uv sync --inexact --dry-run 2>&1 || true)
    while IFS='=' read -r pkg _ || [ -n "$pkg" ]; do
        [ -z "$pkg" ] && continue
        local pkg_hyph
        pkg_hyph=$(echo "$pkg" | tr '_' '-')
        if echo "$dry_output" | grep -qi "$pkg_hyph"; then
            echo "true"
            return
        fi
    done < "$_CUSTOM_BACKUP/_versions"
    echo "false"
}

_safe_sync_print_affected() {
    local dry_output
    dry_output=$(uv sync --inexact --dry-run 2>&1 || true)
    while IFS='=' read -r pkg _ || [ -n "$pkg" ]; do
        [ -z "$pkg" ] && continue
        local pkg_hyph
        pkg_hyph=$(echo "$pkg" | tr '_' '-')
        echo "$dry_output" | grep -i "$pkg_hyph" | sed 's/^/         /'
    done < "$_CUSTOM_BACKUP/_versions"
}

safe_sync_run() {
    _safe_sync_init_paths
    if [ -z "$(_safe_sync_list_files)" ]; then
        uv sync --inexact --quiet
        return
    fi
    _safe_sync_backup_custom
    if [ ! -f "$_CUSTOM_BACKUP/_versions" ]; then
        uv sync --inexact --quiet
        return
    fi

    local touches
    touches=$(_safe_sync_would_touch_custom)
    if [ "$touches" = "false" ]; then
        uv sync --inexact --quiet
        rm -rf "$_CUSTOM_BACKUP"
        return
    fi

    if [ "$SAFE_SYNC_MODE" = "non-interactive" ]; then
        echo ""
        echo "[safe_sync] WARN: uv sync would modify custom-compiled packages — SKIPPED to preserve build."
        _safe_sync_print_affected
        echo "[safe_sync] To update deps interactively, run: bash scripts/safe_sync.sh"
        echo ""
        rm -rf "$_CUSTOM_BACKUP"
        return
    fi

    echo ""
    echo "[WARN] uv sync would modify custom-compiled packages:"
    _safe_sync_print_affected
    echo ""
    local reply
    read -r -p "Proceed with uv sync? (backup/restore will protect custom builds) [y/N] " reply
    if [[ ! "$reply" =~ ^[Yy]$ ]]; then
        echo "[safe_sync] Aborted — using existing venv."
        rm -rf "$_CUSTOM_BACKUP"
        return
    fi
    uv sync --inexact --quiet
    _safe_sync_restore_if_downgraded
}
