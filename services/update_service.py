"""Update detection: which build is running, and whether a newer one exists.

Two jobs that look like one:

1. RECORD what is running. `record_launch()` writes a row into `update_event`
   the first time a given version is seen, with the OS and the install method
   of the moment. This is local history, never sent anywhere; during the 1-to-1
   alpha it is how a tester can answer "which build am I actually on".

2. CHECK whether GitHub has a newer release, in a background thread, and store
   the answer in user settings. The UI reads the STORED answer, so no render
   ever waits on the network. On the very first launch there is nothing stored
   and no banner appears; from the next launch on, the banner reflects the
   previous check.

WHY the check lives here and not in desktop/launcher.py: the launcher only
exists for the frozen desktop bundle. The .deb and .rpm start Streamlit through
packaging/linux/launch.sh, and a source install starts it with `streamlit run`.
Putting the check in the app is the only placement that reaches every install
method on every OS.
"""
from __future__ import annotations

import json
import platform
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from db.models import UpdateEvent
from services.app_info import get_build_info
from support.logging import setup_logging

logger = setup_logging()

# Same idiom as every other module that needs the data dir (core/model_manager,
# ui/widgets/model_download_banner, ...). Note that packaging/windows/install.ps1
# writes its own flags under %APPDATA%\Spendif.ai instead, which no Python code
# reads: see the note on AI-317 in the backlog.
_SPENDIFAI_HOME = Path.home() / ".spendifai"

# Written by the installer that put this copy on the machine. Absent for a DMG
# dragged to /Applications, which has no install hook at all: see _install_method.
_INSTALL_METHOD_FILE = _SPENDIFAI_HOME / ".install_method"

_KNOWN_METHODS = {"homebrew", "dmg", "git", "deb", "rpm", "msix", "winget"}

_LATEST_RELEASE_URL = "https://api.github.com/repos/spendifai/spendif-ai/releases/latest"
_HTTP_TIMEOUT = 5.0

_SK_ENABLED = "update_check_enabled"
_SK_LATEST = "update_latest_known"
_SK_CHECKED_AT = "update_last_checked_at"


def _os_name() -> str:
    """darwin | windows | linux, matching the values stored in update_event."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def _frozen() -> bool:
    """True when running from a PyInstaller bundle rather than from source."""
    return getattr(sys, "frozen", False)


def _install_method() -> str:
    """How this copy got onto the machine.

    Read from a marker file written by whichever installer ran, because it is
    the only party that knows. Guessing from the filesystem does not work: a
    Homebrew cask and a hand-dragged DMG put an identical bundle in an identical
    place, and the difference decides which upgrade command we print.

    The fallbacks are deliberately narrow. A frozen build with no marker is a
    DMG dragged to Applications, the one install path that has no hook able to
    write one. Anything else is running from source.
    """
    try:
        if _INSTALL_METHOD_FILE.is_file():
            value = _INSTALL_METHOD_FILE.read_text(encoding="utf-8").strip().lower()
            if value in _KNOWN_METHODS:
                return value
            logger.warning("update_service: unknown install method %r, ignoring", value)
    except OSError:
        pass  # unreadable marker is not worth failing a launch over

    if _frozen():
        return "dmg" if _os_name() == "darwin" else "unknown"
    return "git"


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """Parse "1.2.3" into (1, 2, 3). None when it is not a plain release."""
    parts = raw.strip().lstrip("v").split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(candidate: str, current: str) -> bool:
    """True when `candidate` is a released version strictly newer than `current`.

    Anything unparseable on either side answers False. That covers the source
    checkout, whose version is the literal "dev": a developer must not be told
    that 0.2.1 is an upgrade from their working tree.
    """
    a, b = _parse_version(candidate), _parse_version(current)
    if a is None or b is None:
        return False
    return a > b


class UpdateService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    # ── settings passthrough ────────────────────────────────────────────────
    # Read straight through the repository rather than SettingsService, which
    # would import this module's caller and close a cycle.

    def _get_setting(self, key: str, default: str = "") -> str:
        from db.repository import get_user_setting
        s = self._Session()
        try:
            return get_user_setting(s, key, default) or default
        finally:
            s.close()

    def _set_setting(self, key: str, value: str) -> None:
        from db.repository import set_user_setting
        s = self._Session()
        try:
            set_user_setting(s, key, value)
            s.commit()
        finally:
            s.close()

    def is_check_enabled(self) -> bool:
        return self._get_setting(_SK_ENABLED, "true").lower() == "true"

    # ── 1. record what is running ───────────────────────────────────────────

    def record_launch(self) -> UpdateEvent | None:
        """Write an update_event row when the running version is new to this DB.

        Returns the row written, or None when this version is already the most
        recent one recorded, which is the common case on every launch after the
        first one following an upgrade.
        """
        version, _ = get_build_info()
        s = self._Session()
        try:
            last = (
                s.query(UpdateEvent)
                .order_by(UpdateEvent.id.desc())
                .first()
            )
            if last is not None and last.version == version:
                return None

            if last is None:
                event = "install"
            elif is_newer(version, last.version):
                event = "upgrade"
            elif is_newer(last.version, version):
                event = "downgrade"
            else:
                # Neither is a parseable release (a source checkout reporting
                # "dev", say). Recording it as an install keeps the history
                # honest without inventing a direction.
                event = "install"

            row = UpdateEvent(
                version=version,
                previous_version=last.version if last is not None else None,
                event=event,
                install_method=_install_method(),
                os_name=_os_name(),
                os_version=platform.release()[:64] or None,
                arch=platform.machine()[:16] or None,
                detected_at=datetime.now(timezone.utc),
            )
            s.add(row)
            s.commit()
            logger.info(
                "update_service: recorded %s to %s (%s, %s)",
                event, version, row.os_name, row.install_method,
            )
            return row
        finally:
            s.close()

    def history(self, limit: int = 50) -> list[UpdateEvent]:
        """Recorded versions, newest first. Read by the Settings page."""
        s = self._Session()
        try:
            return (
                s.query(UpdateEvent)
                .order_by(UpdateEvent.id.desc())
                .limit(limit)
                .all()
            )
        finally:
            s.close()

    # ── 2. check GitHub for a newer release ─────────────────────────────────

    def start_background_check(self) -> None:
        """Ask GitHub for the newest release, without blocking the caller.

        A no-op when the user turned the check off. Failures are swallowed: an
        app that cannot reach GitHub is an app running offline, which is the
        normal case here, not an error worth showing anyone.
        """
        if not self.is_check_enabled():
            return

        def _run() -> None:
            try:
                req = urllib.request.Request(
                    _LATEST_RELEASE_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        # Identifies the app, carries nothing about the user.
                        "User-Agent": "Spendif.ai-update-check",
                    },
                )
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                tag = str(payload.get("tag_name", "")).lstrip("v")
                if not _parse_version(tag):
                    return
                self._set_setting(_SK_LATEST, tag)
                self._set_setting(
                    _SK_CHECKED_AT,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
                logger.info("update_service: newest published release is %s", tag)
            except Exception as exc:  # noqa: BLE001 - never fail a launch for this
                logger.debug("update_service: check skipped (%s)", exc)

        threading.Thread(target=_run, name="update-check", daemon=True).start()

    def pending_update(self) -> dict[str, str] | None:
        """What the sidebar needs to render, or None when there is nothing to say.

        Reads only stored state, so it costs one query and never touches the
        network. Keys: latest, current, install_method, os_name.
        """
        if not self.is_check_enabled():
            return None
        current, _ = get_build_info()
        latest = self._get_setting(_SK_LATEST, "")
        if not latest or not is_newer(latest, current):
            return None
        return {
            "latest": latest,
            "current": current,
            "install_method": _install_method(),
            "os_name": _os_name(),
        }
