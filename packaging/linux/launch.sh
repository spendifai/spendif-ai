#!/bin/bash
# =============================================================================
#  Spendif.ai — user-space launcher for the Linux .deb / .rpm bundle.
#
#  Runs as the USER (gnome-shell / kde-plasma spawn it from the .desktop
#  Exec= line, not as root). The system package (.deb/.rpm) shipped this
#  file to /opt/spendifai/launch.sh and the .desktop file points at it.
#
#  Phases:
#    1. Locate `uv` (system install in /usr/local/bin, else user fallback).
#    2. On first launch (or after a package upgrade): run `uv sync` against
#       the user-owned ~/.spendifai/.venv with the project's pyproject.toml.
#       Shows a zenity --progress --pulsate dialog while uv is working so
#       the user gets visible feedback instead of staring at a still icon.
#    3. Seed ~/.spendifai/.env if missing.
#    4. Exec the pywebview launcher inside the venv's Python.
#
#  All console output goes through tee into ~/.spendifai/launch.log so
#  debugging a desktop-spawned run is possible after the fact.
# =============================================================================
set -eo pipefail        # pipefail so `... | tail` does not swallow uv errors

APP_DIR="/opt/spendifai"
USER_HOME_DIR="$HOME/.spendifai"
VENV_DIR="$USER_HOME_DIR/.venv"
LOG_FILE="$USER_HOME_DIR/launch.log"

mkdir -p "$USER_HOME_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== launch.sh $(date -Iseconds) ==="

# ── Mirror the install method into this user's data dir ─────────────────────
# The package postinst runs as root and cannot know which users will launch the
# app, so it writes the marker next to the code. The app only ever reads
# ~/.spendifai (see services/update_service.py), so copy it here, every launch:
# doing it once would miss the case of a machine that switches from .deb to
# .rpm, or a second user who first launches after the install.
if [[ -r "$APP_DIR/.install_method" ]]; then
  cp -f "$APP_DIR/.install_method" "$USER_HOME_DIR/.install_method" 2>/dev/null || true
fi

# ── Zenity helper ───────────────────────────────────────────────────────────
# Shows a GTK pulsate dialog with the given message while the next command
# runs. Falls back to a silent run when zenity is missing (CI smoke tests).
_with_progress() {
  local title="$1"
  local text="$2"
  shift 2
  if command -v zenity &>/dev/null; then
    zenity --progress --pulsate --auto-close --auto-kill --no-cancel \
           --title="$title" --text="$text" --width=480 </dev/null &
    local zen_pid=$!
    local rc=0
    "$@" || rc=$?
    kill "$zen_pid" 2>/dev/null || true
    wait "$zen_pid" 2>/dev/null || true
    return $rc
  else
    "$@"
  fi
}

# ── 1. Find uv ──────────────────────────────────────────────────────────────
UV=""
for candidate in /usr/local/bin/uv "$HOME/.local/bin/uv" /usr/bin/uv; do
  if [ -x "$candidate" ]; then UV="$candidate"; break; fi
done
if [ -z "$UV" ]; then
  echo "uv not found — installing in user home (postinst should have placed it system-wide)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi
echo "uv: $UV"

# ── 2. Sync user venv (idempotent — fast if already aligned) ────────────────
# We use a marker file (.venv/.spendifai_ready) to detect "first time setup is
# needed". The marker is created ONLY after a successful uv sync. If the venv
# directory exists but the marker doesn't (e.g. previous launch crashed during
# the long llama-cpp-python build), we still treat this as a first launch so
# the user gets the zenity progress dialog instead of staring at a spinner.
READY_MARKER="$VENV_DIR/.spendifai_ready"
IS_FIRST_LAUNCH=false
if [ ! -f "$READY_MARKER" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
  IS_FIRST_LAUNCH=true
  if [ -d "$VENV_DIR" ]; then
    echo "Venv exists but setup never completed — treating as first launch"
  else
    echo "First launch — creating $VENV_DIR"
  fi
  echo "This compiles llama-cpp-python natively (3-8 min on arm64; faster on amd64)."

  # Create the venv with SYSTEM site-packages exposed. This lets pywebview
  # find `gi` (python3-gi) and `cairo` (python3-cairo) — system-managed,
  # ABI-matched to the system libgirepository / libcairo, no pip compile.
  # Use the system Python to keep ABI compatibility with system extension
  # modules (.so files).
  if [ ! -d "$VENV_DIR" ]; then
    SYSTEM_PYTHON="$(command -v python3 || echo /usr/bin/python3)"
    echo "Using system Python: $SYSTEM_PYTHON ($($SYSTEM_PYTHON --version 2>&1))"
    "$UV" venv --python "$SYSTEM_PYTHON" --system-site-packages "$VENV_DIR"
  fi
fi

# Detect NVIDIA GPU (best-effort)
if command -v nvidia-smi &>/dev/null; then
  export CMAKE_ARGS="-DGGML_CUDA=on"
  export FORCE_CMAKE=1
fi

cd "$APP_DIR"

# Always point uv at the per-user venv (uv defaults to .venv inside the
# project dir, which is /opt — read-only). Exported BEFORE _with_progress
# so the subprocess inherits it.
export UV_PROJECT_ENVIRONMENT="$VENV_DIR"

# `--frozen` so uv does not try to update /opt/spendifai/uv.lock at runtime
# (that file lives in a read-only system directory). The lockfile shipped
# with the .deb / .rpm is canonical — we just install from it.
UV_SYNC_FLAGS=(sync --extra desktop --frozen)

# Pulsate dialog only on first launch — afterwards uv sync is sub-second.
if $IS_FIRST_LAUNCH; then
  _with_progress \
    "Spendif.ai — Primo avvio" \
    "Sto preparando l'ambiente AI (3–8 min).\nQuesta è una sola volta.\nNon chiudere questa finestra." \
    "$UV" "${UV_SYNC_FLAGS[@]}" || {
      echo "uv sync failed, retrying CPU-only (in same venv)..."
      unset CMAKE_ARGS FORCE_CMAKE
      _with_progress \
        "Spendif.ai — Primo avvio" \
        "Compilazione GPU fallita, riprovo CPU-only (qualche minuto in più)." \
        "$UV" "${UV_SYNC_FLAGS[@]}"
    }
else
  "$UV" "${UV_SYNC_FLAGS[@]}" || {
    echo "uv sync failed, retrying CPU-only..."
    unset CMAKE_ARGS FORCE_CMAKE
    "$UV" "${UV_SYNC_FLAGS[@]}"
  }
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "FATAL: venv exists but $VENV_DIR/bin/python is missing."
  echo "Inspect ~/.spendifai/launch.log, then:"
  echo "  rm -rf $VENV_DIR && /opt/spendifai/launch.sh"
  if command -v zenity &>/dev/null; then
    zenity --error --title="Spendif.ai" --width=480 \
      --text="Setup non completato. Vedi ~/.spendifai/launch.log e riprova:\nrm -rf ~/.spendifai/.venv && /opt/spendifai/launch.sh"
  fi
  exit 1
fi
# ── 2b. SSM build — first launch only ───────────────────────────────────────
# Compiles llama-cpp-python from git with GPU support so Qwen 3.5 9B (and
# future SSM-hybrid models) work. Runs once; protected by SSM_MARKER so
# subsequent uv syncs don't re-run the 3-8 min compile.
SSM_MARKER="$VENV_DIR/.ssm_built"
if $IS_FIRST_LAUNCH && [ ! -f "$SSM_MARKER" ]; then
  echo "▸ Building llama-cpp-python with SSM support (first launch only)…"
  _ssm_ok=true
  PYTHON="$VENV_DIR/bin/python" \
    bash "$APP_DIR/scripts/setup_ssm_build.sh" --yes --no-custom-list || _ssm_ok=false
  if $_ssm_ok; then
    touch "$SSM_MARKER"
    echo "✔ SSM build complete — Qwen 3.5 9B models now available"
  else
    echo "⚠ SSM build failed — app will work but Qwen 3.5 9B models are unavailable"
  fi
elif [ -f "$SSM_MARKER" ]; then
  # Protect the SSM build: uv sync --no-reinstall-package keeps the git-compiled
  # llama-cpp-python and only updates other packages when the lockfile changes.
  echo "SSM build present — using --no-reinstall-package llama-cpp-python"
  UV_PROJECT_ENVIRONMENT="$VENV_DIR" \
    "$UV" sync --extra desktop --frozen --no-reinstall-package llama-cpp-python 2>/dev/null || true
fi

# Mark the venv as ready so subsequent launches skip the slow first-launch
# code path AND, equally important, skip the zenity progress dialog.
touch "$READY_MARKER"
echo "venv ready: $VENV_DIR"

# ── 3. Seed .env if missing (writable in USER_HOME, not in /opt) ────────────
ENV_FILE="$USER_HOME_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
SPENDIFAI_DB=sqlite:///$USER_HOME_DIR/ledger.db
LLM_BACKEND=local_llama_cpp
EOF
fi

# ── 4. Launch the pywebview app ─────────────────────────────────────────────
cd "$APP_DIR"
exec "$VENV_DIR/bin/python" -m desktop.launcher
