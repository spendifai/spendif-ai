"""Adding the Siri command from the Settings (spec 32, 4.9): the folder is
saved and the command marked as installed only when Shortcuts was opened."""
from __future__ import annotations

from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import create_tables
from services.expense_confirm_service import ExpenseConfirmService
from services.settings_service import SettingsService


def _block(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    """The install block alone, on the database at *db_path*.

    Args:
        db_path: path of the SQLite file.
    """
    from sqlalchemy import create_engine
    from services.settings_service import SettingsService
    from ui.i18n import set_language
    from ui.siri_install import render_siri_install
    set_language("it")
    cfg = SettingsService(create_engine(f"sqlite:///{db_path}"))
    render_siri_install(cfg, cfg.get_all(), key="t")


def test_the_button_installs_with_the_chosen_folder(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    eng = create_tables(create_engine(f"sqlite:///{db}"))
    called = []
    monkeypatch.setattr(ExpenseConfirmService, "install_siri_shortcut",
                        staticmethod(lambda folder, language, name="": called.append((folder, language, name))))
    app = AppTest.from_function(_block, args=(str(db),), default_timeout=60)
    app.run()
    app.text_input(key="t_siri_folder").set_value("Le mie spese").run()
    app.button(key="t_siri_install").click().run()
    assert not app.exception, app.exception
    assert called == [("Le mie spese", "it", "Segna spesa")]
    cfg = SettingsService(eng)
    assert (cfg.get("siri_enabled"), cfg.get("siri_folder")) == ("true", "Le mie spese")


def test_a_failed_signing_installs_nothing(tmp_path, monkeypatch):
    db = tmp_path / "f.db"
    eng = create_tables(create_engine(f"sqlite:///{db}"))

    def refuse(folder, language, name=""):
        raise RuntimeError("no")
    monkeypatch.setattr(ExpenseConfirmService, "install_siri_shortcut", staticmethod(refuse))
    app = AppTest.from_function(_block, args=(str(db),), default_timeout=60)
    app.run()
    app.button(key="t_siri_install").click().run()
    assert app.error and SettingsService(eng).get("siri_enabled") is None



def test_several_phrases_are_several_commands(monkeypatch, tmp_path):
    import subprocess
    import sys
    from services import expense_confirm_service as mod
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    monkeypatch.setattr(mod.Path, "home", staticmethod(lambda: tmp_path))
    signed, opened = [], []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["shortcuts", "sign"]:
            out = cmd[cmd.index("--output") + 1]
            open(out, "wb").write(b"x")
            signed.append(out)
        elif cmd[0] == "open":
            opened.append(cmd[1])
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    files = ExpenseConfirmService.install_siri_shortcut("Spendif.ai", "it", "Segna spesa, Registra scontrino, segna spesa ,")
    assert [f.name for f in files] == ["Segna spesa.shortcut", "Registra scontrino.shortcut"]   # Siri ignores case
    assert len(opened) == 2
