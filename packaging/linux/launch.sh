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
# directory exists but the marker doesn't (e.g. a previous launch was killed
# halfway through the download), we still treat this as a first launch so
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
  echo "Downloading the Python environment (no compilation: see pyproject.toml)."

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

# ── GPU: say what is there, and use it when we can ──────────────────────────
# Nothing is compiled here any more: pyproject.toml resolves llama-cpp-python
# from a prebuilt-wheel index, so `uv sync` downloads a CPU build. An NVIDIA
# card can do better and the same upstream index publishes a CUDA build, so we
# swap the wheel after the sync. An AMD card cannot be used yet - nobody
# publishes a prebuilt Vulkan wheel and we have no AMD machine to test one we
# built - but telling the owner of a 16 GB Radeon that no GPU was detected was
# false, and false is worse than unsupported.
GPU_VENDOR="none"
GPU_NAME=""
if command -v nvidia-smi &>/dev/null; then
  GPU_VENDOR="nvidia"
  GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
elif command -v lspci &>/dev/null; then
  gpu_line="$(lspci 2>/dev/null | grep -iE 'VGA|3D controller|Display controller' | head -1 || true)"
  if [ -n "$gpu_line" ]; then
    GPU_NAME="${gpu_line#*: }"
    case "$gpu_line" in
      *NVIDIA*)                 GPU_VENDOR="nvidia" ;;
      *AMD*|*ATI*|*Radeon*)     GPU_VENDOR="amd" ;;
      *Intel*)                  GPU_VENDOR="intel" ;;
    esac
  fi
fi
case "$GPU_VENDOR" in
  nvidia) echo "GPU: ${GPU_NAME:-NVIDIA} - will install the CUDA build" ;;
  amd)    echo "GPU: ${GPU_NAME:-AMD} - GPU acceleration for AMD is not available yet, the model runs on the CPU" ;;
  intel)  echo "GPU: ${GPU_NAME:-Intel} - the model runs on the CPU" ;;
  *)      echo "GPU: none detected - the model runs on the CPU" ;;
esac

cd "$APP_DIR"

# Always point uv at the per-user venv (uv defaults to .venv inside the
# project dir, which is /opt — read-only). Exported BEFORE _with_progress
# so the subprocess inherits it.
export UV_PROJECT_ENVIRONMENT="$VENV_DIR"

# `--frozen` so uv does not try to update /opt/spendifai/uv.lock at runtime
# (that file lives in a read-only system directory). The lockfile shipped
# with the .deb / .rpm is canonical — we just install from it.
UV_SYNC_FLAGS=(sync --extra desktop --frozen)

# A llama-cpp-python that is not the one in the lockfile - the CUDA wheel, or
# a local SSM build - must survive the sync, or every launch would undo it.
if [ -f "$VENV_DIR/.cuda_wheel" ] || [ -f "$VENV_DIR/.ssm_built" ]; then
  UV_SYNC_FLAGS+=(--no-reinstall-package llama-cpp-python)
fi

# ── Failure has to be visible ───────────────────────────────────────────────
# This script is normally started by the .desktop entry, with no terminal
# attached. Until 2026-09-22 a failed sync ended the script through `set -e`
# BEFORE the error message and the zenity dialog below could run: the user
# double-clicked the icon and nothing whatsoever happened. So the exit status
# is captured by hand and the dialog is the last thing that runs.
_fail() {
  local headline="$1"
  local detail="$2"
  echo "FATAL: $headline"
  echo "$detail"
  echo "Full log: $LOG_FILE"
  if command -v zenity &>/dev/null; then
    zenity --error --title="Spendif.ai" --width=520 \
      --text="${headline}\n\n${detail}\n\nDettagli in ${LOG_FILE}" 2>/dev/null || true
  fi
  exit 1
}

sync_rc=0
if $IS_FIRST_LAUNCH; then
  _with_progress \
    "Spendif.ai — Primo avvio" \
    "Sto preparando l'ambiente AI.\nQuesta è una sola volta.\nNon chiudere questa finestra." \
    "$UV" "${UV_SYNC_FLAGS[@]}" || sync_rc=$?
else
  "$UV" "${UV_SYNC_FLAGS[@]}" || sync_rc=$?
fi

if [ "$sync_rc" -ne 0 ]; then
  # A download that cannot resolve a name reads exactly like a broken install
  # unless we say which one it is. On Arch, on 2026-09-22, it was the DNS.
  if tail -n 40 "$LOG_FILE" 2>/dev/null | grep -qiE 'dns error|name or service not known|temporary failure in name resolution|tunnel error|failed to fetch'; then
    _fail "Non sono riuscito a scaricare i componenti." \
          "Sembra un problema di rete di questo computer (DNS o proxy), non dell'applicazione.\nControlla la connessione e riprova ad avviare Spendif.ai."
  fi
  _fail "Preparazione dell'ambiente non riuscita." \
        "Riprova ad avviare l'applicazione. Se fallisce di nuovo:\n  rm -rf $VENV_DIR && /opt/spendifai/launch.sh"
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  _fail "L'ambiente è incompleto." \
        "Ricrealo con:\n  rm -rf $VENV_DIR && /opt/spendifai/launch.sh"
fi

# ── CUDA wheel for NVIDIA cards (first launch only) ─────────────────────────
# Pinned to the very version the lockfile holds: an unpinned install here would
# quietly put a different llama-cpp-python on GPU machines than on every other.
if [ "$GPU_VENDOR" = "nvidia" ] && [ ! -f "$VENV_DIR/.cuda_wheel" ]; then
  llama_version="$(awk '/name = "llama-cpp-python"/ { getline; gsub(/[^0-9.]/, "", $0); print; exit }' "$APP_DIR/uv.lock")"
  if [ -n "$llama_version" ]; then
    echo "▸ Installing the CUDA build of llama-cpp-python ${llama_version}..."
    if "$UV" pip install "llama-cpp-python==${llama_version}" \
         --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cu124" \
         --force-reinstall --no-deps \
       && "$VENV_DIR/bin/python" -c "import llama_cpp" >/dev/null 2>&1; then
      touch "$VENV_DIR/.cuda_wheel"
      echo "✔ CUDA build in place: the model runs on the GPU"
    else
      # Installing is not the test; importing is. A CUDA wheel installs happily
      # on a machine whose driver cannot load it.
      echo "⚠ CUDA build unusable on this machine: restoring the CPU build"
      "$UV" sync --extra desktop --frozen || true
    fi
  fi
fi

# ── SSM build, opt in and never automatic ─────────────────────────────────────
# Compiling llama-cpp-python from git adds Qwen 3.5 9B and other SSM-hybrid
# architectures. It also needs a full C/C++ toolchain and several minutes, on
# a machine that has just been told the setup is one click. Until 2026-09-22
# it ran on every first launch: on a Ubuntu without g++ it burned the time and
# failed, and where it succeeded it replaced a known-good wheel with a local
# build nobody had tested. It is now explicit:
#
#   SPENDIFAI_SSM_BUILD=1 /opt/spendifai/launch.sh
#
SSM_MARKER="$VENV_DIR/.ssm_built"
if [ "${SPENDIFAI_SSM_BUILD:-0}" = "1" ] && [ ! -f "$SSM_MARKER" ]; then
  echo "▸ Building llama-cpp-python with SSM support (this takes several minutes)..."
  if PYTHON="$VENV_DIR/bin/python" \
       bash "$APP_DIR/scripts/setup_ssm_build.sh" --yes --no-custom-list; then
    touch "$SSM_MARKER"
    echo "✔ SSM build complete: Qwen 3.5 9B models now available"
  else
    echo "⚠ SSM build failed: the application still works, Qwen 3.5 9B models do not"
  fi
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
