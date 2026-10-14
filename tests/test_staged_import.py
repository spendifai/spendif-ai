"""Files prepared per account in the Check List, run by the Import page with
their account already assigned."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import create_tables
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _keep_language():
    before = get_language()
    yield
    set_language(before)


# ── Holding files ────────────────────────────────────────────────────────────

def _holding_script() -> None:
    import streamlit as st

    from ui.widgets.staged_import import (
        StagedFile, stage_file, staged_files, take_staged_files, unstage_file,
    )

    ok = [
        stage_file(StagedFile("sep.csv", b"a", "BBVA")),
        stage_file(StagedFile("oct.csv", b"b", "BBVA")),
        stage_file(StagedFile("sep.csv", b"c", "Amex")),     # same name, other account
        stage_file(StagedFile("oct.csv", b"d", "BBVA")),     # picked again, same account
    ]
    st.write(f"OK:{ok}")
    st.write(f"HELD:{[(f.name, f.account, f.data) for f in staged_files()]}")
    unstage_file("sep.csv")
    st.write(f"AFTER:{[f.name for f in staged_files()]}")
    taken = take_staged_files()
    st.write(f"TAKEN:{[f.name for f in taken]} LEFT:{staged_files()}")


def test_files_are_held_per_account_by_name():
    app = AppTest.from_function(_holding_script).run(timeout=30)
    assert not app.exception, app.exception
    out = [m.value for m in app.markdown]
    assert "OK:[True, True, False, True]" in out
    assert "HELD:[('sep.csv', 'BBVA', b'a'), ('oct.csv', 'BBVA', b'd')]" in out
    assert "AFTER:['oct.csv']" in out
    assert "TAKEN:['oct.csv'] LEFT:[]" in out


# ── Import page ──────────────────────────────────────────────────────────────

def _import_script(db_path: str, known_format: bool) -> None:
    import streamlit as st
    from sqlalchemy import create_engine

    import ui.upload_page as up
    from services.import_service import ImportService
    from ui.i18n import set_language
    from ui.widgets.staged_import import StagedFile, stage_file

    set_language("en")
    if "_seeded" not in st.session_state:
        st.session_state["_seeded"] = True
        stage_file(StagedFile("sep.csv", b"date;amount\n", "BBVA"))
        stage_file(StagedFile("amex.csv", b"date;amount\n", "Amex"))

    svc = ImportService(create_engine(f"sqlite:///{db_path}"))

    class _Hit:
        skip_rows = 2

    svc.find_schema_by_header = lambda data, name: _Hit() if known_format else None
    svc.detect_skip_rows = lambda data, name: (0, False)

    def _fake_run(import_svc, items, accounts, skips):
        st.session_state["_ran"] = (sorted(n for n, _ in items), accounts, skips)

    up._run_import = _fake_run
    up._render_after_import = lambda import_svc: None
    up._render_staged(svc, up.staged_files())
    st.write(f"RAN:{st.session_state.get('_ran')}")


def _ledger(tmp_path):
    db_path = tmp_path / "ledger.db"
    create_tables(create_engine(f"sqlite:///{db_path}"))
    return db_path


def test_known_formats_run_at_once_with_their_accounts(tmp_path):
    app = AppTest.from_function(
        _import_script, kwargs={"db_path": str(_ledger(tmp_path)), "known_format": True})
    app.run(timeout=30)
    assert not app.exception, app.exception
    ran = [m.value for m in app.markdown if m.value.startswith("RAN:")][0]
    assert "['amex.csv', 'sep.csv']" in ran
    assert "'sep.csv': 'BBVA'" in ran and "'amex.csv': 'Amex'" in ran
    assert "'sep.csv': 2" in ran                      # header offset from the known format


def test_new_format_waits_for_the_header_question(tmp_path):
    app = AppTest.from_function(
        _import_script, kwargs={"db_path": str(_ledger(tmp_path)), "known_format": False})
    app.run(timeout=30)
    assert not app.exception, app.exception
    assert "RAN:None" in [m.value for m in app.markdown]
    assert [n.key for n in app.number_input] == ["file_skip_sep.csv", "file_skip_amex.csv"]
    [b for b in app.button if b.key == "staged_start"][0].click().run(timeout=30)
    ran = [m.value for m in app.markdown if m.value.startswith("RAN:")][0]
    assert "'sep.csv': 'BBVA'" in ran
