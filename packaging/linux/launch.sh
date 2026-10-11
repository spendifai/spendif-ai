#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai - launcher wrapper for the Linux packages
#
#  WHAT IT DOES NOW
#    Starts the bundled application. That is all.
#
#  WHAT IT USED TO DO, AND WHY IT STOPPED
#    It located uv, ran a sync against the distribution's Python into a
#    per-user virtual environment, showed a progress dialog while that
#    happened, and only then started the application. Every one of those steps
#    could fail on a machine we had never seen, and several did: the supported
#    Python range was missed on both sides in a single day, once because a
#    distribution had moved ahead and once because another had not.
#
#    The package now carries its own Python and its own dependencies, so the
#    first launch has nothing left to install and nothing left to compile.
#
#  THE INTERFACE OPENS IN THE BROWSER
#    There is no native window on Linux: PyGObject publishes no wheels and
#    pycairo publishes them for Windows only, so a bundled Python cannot use
#    the distribution's python3-gi, which is built for the ABI of the
#    distribution's own Python. The launcher detects this and opens the
#    default browser instead.
# =============================================================================
set -euo pipefail

INSTALL_ROOT="/opt/spendifai"
USER_HOME_DIR="${HOME}/.spendifai"
LOG_FILE="${USER_HOME_DIR}/spendifai-launcher.log"

mkdir -p "${USER_HOME_DIR}"

# Record how this copy was installed, for the update check and the technical
# report. The package wrote the same marker next to the code, which is shared
# by every user on the machine; this mirrors it into the home of whoever is
# actually launching.
if [[ -f "${INSTALL_ROOT}/.install_method" ]]; then
  cp -f "${INSTALL_ROOT}/.install_method" "${USER_HOME_DIR}/.install_method" 2>/dev/null || true
fi

BINARY="${INSTALL_ROOT}/SpendifAi"
if [[ ! -x "${BINARY}" ]]; then
  echo "Spendif.ai: ${BINARY} is missing or not executable." >&2
  echo "The package looks incomplete; reinstalling it is the fix." >&2
  exit 1
fi

exec "${BINARY}" "$@"
