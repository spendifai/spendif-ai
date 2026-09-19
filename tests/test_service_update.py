"""Tests for UpdateService: version comparison, install method, event recording."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from db.models import Base, UpdateEvent
from services import update_service
from services.update_service import UpdateService, is_newer


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def svc(engine):
    return UpdateService(engine)


# ── Version comparison ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("0.2.2", "0.2.1", True),
        ("0.3.0", "0.2.9", True),
        ("1.0.0", "0.9.9", True),
        ("0.2.1", "0.2.1", False),
        ("0.2.0", "0.2.1", False),
        # Double-digit components must compare numerically, not as strings:
        # "0.2.10" sorts before "0.2.9" alphabetically and after it as a version.
        ("0.2.10", "0.2.9", True),
        ("0.2.9", "0.2.10", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert is_newer(candidate, current) is expected


def test_is_newer_refuses_unparseable_versions():
    """A source checkout reports "dev". It must never be told to upgrade:
    there is nothing to compare, and the working tree is usually ahead."""
    assert is_newer("0.2.1", "dev") is False
    assert is_newer("dev", "0.2.1") is False
    assert is_newer("0.2.1-rc1", "0.2.0") is False


def test_is_newer_tolerates_the_v_prefix():
    """GitHub tags carry a leading v; releases do not."""
    assert is_newer("v0.2.2", "0.2.1") is True


# ── Install method ────────────────────────────────────────────────────────────

def test_install_method_reads_the_marker(tmp_path, monkeypatch):
    marker = tmp_path / ".install_method"
    marker.write_text("homebrew\n", encoding="utf-8")
    monkeypatch.setattr(update_service, "_INSTALL_METHOD_FILE", marker)
    assert update_service._install_method() == "homebrew"


def test_install_method_ignores_an_unknown_value(tmp_path, monkeypatch):
    """A marker we do not recognise must not be echoed into the UI as if it
    were a supported install method: it would select no upgrade command."""
    marker = tmp_path / ".install_method"
    marker.write_text("snap\n", encoding="utf-8")
    monkeypatch.setattr(update_service, "_INSTALL_METHOD_FILE", marker)
    monkeypatch.setattr(update_service, "_frozen", lambda: False)
    assert update_service._install_method() == "git"


def test_install_method_defaults_to_dmg_for_a_frozen_mac_build(tmp_path, monkeypatch):
    """Dragging a DMG to Applications runs no installer, so nothing writes a
    marker. On macOS that absence is itself the answer."""
    monkeypatch.setattr(update_service, "_INSTALL_METHOD_FILE", tmp_path / "absent")
    monkeypatch.setattr(update_service, "_frozen", lambda: True)
    monkeypatch.setattr(update_service, "_os_name", lambda: "darwin")
    assert update_service._install_method() == "dmg"


def test_install_method_defaults_to_git_when_running_from_source(tmp_path, monkeypatch):
    monkeypatch.setattr(update_service, "_INSTALL_METHOD_FILE", tmp_path / "absent")
    monkeypatch.setattr(update_service, "_frozen", lambda: False)
    assert update_service._install_method() == "git"


# ── Event recording ───────────────────────────────────────────────────────────

def test_record_launch_writes_an_install_row_on_a_fresh_db(svc, engine, monkeypatch):
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    row = svc.record_launch()
    assert row is not None
    assert row.version == "0.2.1"
    assert row.previous_version is None
    assert row.event == "install"
    assert row.os_name in {"darwin", "windows", "linux"}


def test_record_launch_is_silent_on_an_unchanged_version(svc, monkeypatch):
    """Every launch calls this. Only a version change may write a row, or the
    table would grow by one row per app start and say nothing extra."""
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    assert svc.record_launch() is not None
    assert svc.record_launch() is None
    assert svc.record_launch() is None
    assert len(svc.history()) == 1


def test_record_launch_records_an_upgrade(svc, monkeypatch):
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    svc.record_launch()
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.3.0", "t"))
    row = svc.record_launch()
    assert row.event == "upgrade"
    assert row.previous_version == "0.2.1"


def test_record_launch_records_a_downgrade(svc, monkeypatch):
    """Reinstalling an older DMG over a newer one is a real thing a tester does
    when a release breaks something."""
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.3.0", "t"))
    svc.record_launch()
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    assert svc.record_launch().event == "downgrade"


def test_history_is_newest_first(svc, monkeypatch):
    for v in ("0.1.0", "0.2.0", "0.3.0"):
        monkeypatch.setattr(update_service, "get_build_info", lambda v=v: (v, "t"))
        svc.record_launch()
    assert [r.version for r in svc.history()] == ["0.3.0", "0.2.0", "0.1.0"]


# ── Pending update ────────────────────────────────────────────────────────────

def test_pending_update_is_none_when_nothing_was_ever_checked(svc, monkeypatch):
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    assert svc.pending_update() is None


def test_pending_update_reports_a_newer_release(svc, monkeypatch):
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    svc._set_setting("update_latest_known", "0.3.0")
    pending = svc.pending_update()
    assert pending["latest"] == "0.3.0"
    assert pending["current"] == "0.2.1"
    assert "install_method" in pending and "os_name" in pending


def test_pending_update_is_none_when_already_current(svc, monkeypatch):
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.3.0", "t"))
    svc._set_setting("update_latest_known", "0.3.0")
    assert svc.pending_update() is None


def test_pending_update_is_none_when_the_user_turned_the_check_off(svc, monkeypatch):
    """Disabling the check must also silence the badge, not just the request:
    otherwise a stale stored answer keeps nagging forever."""
    monkeypatch.setattr(update_service, "get_build_info", lambda: ("0.2.1", "t"))
    svc._set_setting("update_latest_known", "0.3.0")
    svc._set_setting("update_check_enabled", "false")
    assert svc.pending_update() is None


def test_background_check_does_nothing_when_disabled(svc, monkeypatch):
    """The toggle must gate the network call itself, not only the rendering."""
    called = []
    monkeypatch.setattr(
        update_service.urllib.request, "urlopen",
        lambda *a, **k: called.append(1),
    )
    svc._set_setting("update_check_enabled", "false")
    svc.start_background_check()
    assert called == []


def test_update_event_table_exists_in_the_metadata():
    """The table is created by create_all like every other one: it is new, so
    no hand-written migration in db.models is needed for it."""
    assert "update_event" in Base.metadata.tables
    cols = set(Base.metadata.tables["update_event"].columns.keys())
    assert {"os_name", "install_method", "version", "previous_version"} <= cols


# ── Command selection ─────────────────────────────────────────────────────────

def test_command_key_per_install_method():
    from ui.components.update_checker import _command_key

    assert _command_key("homebrew", "darwin") == "update.cmd.homebrew"
    assert _command_key("dmg", "darwin") == "update.cmd.dmg"
    assert _command_key("deb", "linux") == "update.cmd.deb"
    assert _command_key("", "linux") == "update.cmd.unknown"


def test_command_key_keeps_the_macos_installer_path_off_other_systems():
    """update.cmd.git names ~/Applications/Spendif.ai/packaging/macos/install.sh,
    which exists on macOS only. A source install elsewhere must be sent to
    git pull instead of to a path that is not there."""
    from ui.components.update_checker import _command_key

    assert _command_key("git", "darwin") == "update.cmd.git"
    assert _command_key("git", "linux") == "update.cmd.git_source"
    assert _command_key("git", "windows") == "update.cmd.git_source"


def test_every_command_key_resolves_in_every_locale():
    """A missing key renders as the key itself: the user would read
    "update.cmd.homebrew" where the upgrade command should be."""
    import json
    from pathlib import Path

    from ui.components.update_checker import _COMMAND_KEYS, _FALLBACK_COMMAND_KEY

    keys = set(_COMMAND_KEYS.values()) | {_FALLBACK_COMMAND_KEY, "update.cmd.git_source"}
    keys |= {"update.available_title", "update.version_line", "update.restart_hint"}

    i18n_dir = Path(__file__).resolve().parent.parent / "ui" / "i18n"
    for path in sorted(i18n_dir.glob("*.json")):
        strings = json.loads(path.read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in strings)
        assert not missing, f"{path.name} is missing {missing}"
