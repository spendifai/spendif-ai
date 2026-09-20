"""Sidebar badge telling the user a newer build exists, and how to get it.

Two independent sources feed it, because the two install families learn about
updates in different ways:

* A packaged build (Homebrew cask, DMG, .deb, .rpm, MSIX) reports a version.
  services.update_service compares it with the newest GitHub release at startup
  and leaves the answer in ``st.session_state["update_banner"]``.

* A source install has no version to compare (it reports "dev"), but it does
  have a git remote. The launcher runs a background ``git fetch`` and writes
  ``~/.spendifai/.update_available`` with something like "3 commits behind
  origin/main". That file predates this component and still drives it.

The upgrade command is chosen from the install method, not from the OS alone:
a Homebrew cask and a hand-dragged DMG sit in the same place on the same Mac
and need different commands. See services.update_service._install_method.

Usage:
    from ui.components.update_checker import render_update_warning
    render_update_warning()   # call at top of render_sidebar()
"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from ui.i18n import t

# Path where the source-install launcher writes the git-based update flag.
_UPDATE_FLAG: Path = Path.home() / ".spendifai" / ".update_available"

# How long (seconds) to cache the flag-file check between Streamlit reruns.
# Avoids hitting the filesystem on every rerun (Streamlit can rerun many times
# per second on interaction) while keeping the badge reasonably fresh.
_CACHE_TTL: float = 300.0   # 5 minutes

# Session-state keys
_SK_LAST_CHECK  = "_update_checker_last_check"
_SK_UPDATE_MSG  = "_update_checker_message"   # None → no update available

# Install method → i18n key holding the upgrade instruction. Every method we
# ship an installer for is listed; "unknown" is the honest answer when the
# marker file is missing on a platform whose installers all write one.
_COMMAND_KEYS = {
    "homebrew": "update.cmd.homebrew",
    "dmg":      "update.cmd.dmg",
    "git":      "update.cmd.git",
    "deb":      "update.cmd.deb",
    "rpm":      "update.cmd.rpm",
    "msix":     "update.cmd.msix",
    "winget":   "update.cmd.winget",
}
_FALLBACK_COMMAND_KEY = "update.cmd.unknown"


def _command_key(install_method: str, os_name: str) -> str:
    """i18n key for the upgrade instruction to print.

    Source installs need the OS as well as the method: only macOS has the
    install.sh wrapper under ~/Applications, and printing that path to someone
    who cloned the repo on Linux would send them to a file that is not there.
    """
    if install_method == "git" and os_name != "darwin":
        return "update.cmd.git_source"
    return _COMMAND_KEYS.get(install_method, _FALLBACK_COMMAND_KEY)


def _read_flag() -> str | None:
    """
    Read the update flag file and return its content, or None if absent.

    Returns:
        A non-empty string with the update description
        (e.g. "3 commits behind origin/main"), or None.
    """
    try:
        if _UPDATE_FLAG.is_file():
            content = _UPDATE_FLAG.read_text(encoding="utf-8").strip()
            return content if content else None
        return None
    except OSError:
        # Permission error, race condition with launcher, etc. — ignore silently.
        return None


def _refresh_cache() -> None:
    """
    Re-read the flag file and update session-state cache.
    Called when the cache TTL has expired.
    """
    st.session_state[_SK_LAST_CHECK] = time.time()
    st.session_state[_SK_UPDATE_MSG] = _read_flag()


def _render_version_badge(banner: dict[str, str]) -> None:
    """Badge for a packaged build: we know both versions and the install method."""
    command = t(_command_key(banner.get("install_method", ""), banner.get("os_name", "")))
    st.sidebar.warning(
        f"🔔 **{t('update.available_title')}**\n\n"
        + t("update.version_line", latest=banner["latest"], current=banner["current"])
        + "\n\n"
        + command
        + "\n\n"
        + t("update.restart_hint"),
        icon=None,
    )


def _render_git_badge(detail: str) -> None:
    """Badge for a source install: commits behind the remote, no version to show."""
    from services.update_service import _os_name

    st.sidebar.warning(
        f"🔔 **{t('update.available_title')}** ({detail})\n\n"
        + t(_command_key("git", _os_name()))
        + "\n\n"
        + t("update.restart_hint"),
        icon=None,
    )


def render_update_warning() -> None:
    """
    Render an update warning in the Streamlit sidebar if an update is available.

    Call this at the very top of ``render_sidebar()``, before any other widget.
    It is a no-op when neither source has anything to report, which is the
    normal case.

    Reads only state that is already resolved: the version comparison was done
    once per session in app.py, and the flag file is cached for ``_CACHE_TTL``
    seconds. Nothing here touches the network.
    """
    banner = st.session_state.get("update_banner")
    if banner:
        _render_version_badge(banner)
        return

    now = time.time()
    last_check: float = st.session_state.get(_SK_LAST_CHECK, 0.0)

    if (now - last_check) >= _CACHE_TTL:
        _refresh_cache()

    update_msg: str | None = st.session_state.get(_SK_UPDATE_MSG)

    if update_msg is None:
        # No update available — silent no-op
        return

    _render_git_badge(update_msg)
