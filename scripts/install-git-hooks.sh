#!/usr/bin/env bash
# Install the pre-push hook into this repository and into its sibling repos.
#
# The hook is not versioned where it runs: git never installs hooks by itself,
# and the two documentation repositories have no scripts directory of their
# own. So the source lives here and this script copies it, which also means a
# fresh clone has no protection until somebody runs this once.
#
# Usage:
#   scripts/install-git-hooks.sh                 # this repo plus known siblings
#   scripts/install-git-hooks.sh /path/to/repo   # one specific repository
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_SRC="$SCRIPT_DIR/hooks/pre-push"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT="$(dirname "$REPO_ROOT")"

[ -f "$HOOK_SRC" ] || { echo "missing hook source: $HOOK_SRC" >&2; exit 1; }

if [ $# -gt 0 ]; then
    targets=("$@")
else
    targets=("$REPO_ROOT" "$PARENT/documents" "$PARENT/marketing_artifacts")
fi

installed=0
blocked=0
for repo in "${targets[@]}"; do
    if [ ! -d "$repo/.git" ]; then
        echo "  skipped   $repo (not a git repository)"
        continue
    fi
    # core.hooksPath overrides .git/hooks entirely, and a stale one is how a
    # repository ends up with hooks that are installed and never run: this one
    # pointed at a directory of a project that had been renamed away, so every
    # hook here was dead for months without a single message. Report it instead
    # of installing where git will not look.
    override="$(git -C "$repo" config --get core.hooksPath || true)"
    if [ -n "$override" ]; then
        echo "  BLOCKED   $repo" >&2
        echo "            core.hooksPath = $override" >&2
        if [ -d "$override" ]; then
            echo "            hooks are read from there, not from .git/hooks." >&2
            echo "            Install into that directory, or clear the setting:" >&2
        else
            echo "            that directory does not exist, so NO hook runs in this" >&2
            echo "            repository at all. Clear the setting:" >&2
        fi
        echo "              git -C $repo config --unset core.hooksPath" >&2
        blocked=$((blocked + 1))
        continue
    fi

    hooks_dir="$repo/$(git -C "$repo" rev-parse --git-path hooks)"
    mkdir -p "$hooks_dir"
    if [ -e "$hooks_dir/pre-push" ] && ! cmp -s "$HOOK_SRC" "$hooks_dir/pre-push"; then
        cp "$hooks_dir/pre-push" "$hooks_dir/pre-push.bak"
        echo "  note      existing hook kept as pre-push.bak in $repo"
    fi
    cp "$HOOK_SRC" "$hooks_dir/pre-push"
    chmod +x "$hooks_dir/pre-push"
    echo "  installed $repo"
    installed=$((installed + 1))
done

echo "pre-push hook installed in $installed repositories"
if [ "$blocked" -gt 0 ]; then
    echo "$blocked repositories skipped because core.hooksPath is set" >&2
    exit 1
fi
