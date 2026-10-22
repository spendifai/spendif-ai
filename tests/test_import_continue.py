"""Inside the workflow, the import page ends with a button to the next step."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import create_tables
from services.import_service import ImportService
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _keep_language():
    before = get_language()
    yield
    set_language(before)


def _script(db_path: str, page: str, pending_sign: bool) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    import streamlit as st
    from sqlalchemy import create_engine

    from services.import_service import ImportService
    from ui.i18n import set_language
    from ui.upload_page import _render_guided_continue

    set_language("en")
    st.session_state.setdefault("page", page)
    if pending_sign:
        st.session_state["_pending_sign_confirmations"] = [{"filename": "x.csv"}]
    _render_guided_continue(ImportService(create_engine(f"sqlite:///{db_path}")))
    st.write(f"STEP:{st.session_state.get('_guided_step')}")


def _ledger(tmp_path, status: str | None):
    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    if status:
        svc = ImportService(eng)
        job = svc.create_job(1)
        svc.update_job(job.id, status=status)
    return db_path


def _run(db_path, page="guided", pending_sign=False):
    app = AppTest.from_function(
        _script, kwargs={"db_path": str(db_path), "page": page, "pending_sign": pending_sign},
    )
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def _continue(app):
    return [b for b in app.button if b.key == "guided_import_continue"]


def test_after_the_import_the_button_moves_to_the_counterparts(tmp_path):
    app = _run(_ledger(tmp_path, "completed"))
    (button,) = _continue(app)
    assert button.label == "Continue: check the counterparts"
    button.click().run(timeout=30)
    assert "STEP:counterparts" in [m.value for m in app.markdown]


@pytest.mark.parametrize("status, page, pending_sign", [
    (None, "guided", False),              # nothing imported yet
    ("running", "guided", False),         # import still going
    ("completed", "guided", True),        # a question still waits here
    ("completed", "import", False),       # regular page, outside the workflow
])
def test_no_button_while_something_waits_or_outside_the_workflow(tmp_path, status, page, pending_sign):
    app = _run(_ledger(tmp_path, status), page=page, pending_sign=pending_sign)
    assert _continue(app) == []
