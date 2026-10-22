"""The guided workflow shell: header navigation, leaving and resuming.

Rendered with AppTest; the step pages are replaced by a stub router so the
test exercises only the shell.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from core.guided_flow import STEPS


def _app():
    import streamlit as st
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from db.models import create_tables
    from services.settings_service import SettingsService
    from ui.guided_page import render_guided_page
    from ui.i18n import set_language

    set_language("en")
    if "_engine" not in st.session_state:
        eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        create_tables(eng)
        SettingsService(eng).create_account("BancaX", "")
        st.session_state["_engine"] = eng
        st.session_state["page"] = "guided"
    if st.session_state["page"] == "guided":
        render_guided_page(st.session_state["_engine"], lambda step, _e: st.write(f"STEP:{step}"))
    else:
        st.write(f"PAGE:{st.session_state['page']}")


def _texts(at):
    return [m.value for m in at.markdown]


def _button(at, key):
    return next(b for b in at.button if b.key == key)


def test_starts_from_first_open_step_with_header():
    at = AppTest.from_function(_app).run(timeout=30)
    assert "STEP:checklist" in _texts(at)
    assert at.caption[0].value == "Step 1 of 7: What to load"
    assert _button(at, "guided_back").disabled


def test_next_and_back_move_between_steps():
    at = AppTest.from_function(_app).run(timeout=30)
    _button(at, "guided_next").click().run(timeout=30)
    assert "STEP:import" in _texts(at)
    _button(at, "guided_back").click().run(timeout=30)
    assert "STEP:checklist" in _texts(at)


def test_leave_returns_home_and_resumes_on_same_step():
    at = AppTest.from_function(_app).run(timeout=30)
    _button(at, "guided_next").click().run(timeout=30)
    _button(at, "guided_exit").click().run(timeout=30)
    assert "PAGE:home" in _texts(at)
    at.session_state["page"] = "guided"
    at.run(timeout=30)
    assert "STEP:import" in _texts(at)


def test_done_on_last_step_lands_on_report_and_restarts_cycle():
    at = AppTest.from_function(_app).run(timeout=30)
    for _ in range(len(STEPS) - 1):
        _button(at, "guided_next").click().run(timeout=30)
    assert "STEP:report" in _texts(at)
    assert _button(at, "guided_next").label == "Done"
    _button(at, "guided_next").click().run(timeout=30)
    assert "PAGE:report" in _texts(at)
    at.session_state["page"] = "guided"
    at.run(timeout=30)
    assert "STEP:checklist" in _texts(at)


def test_the_first_time_it_says_every_session_starts_here():
    at = AppTest.from_function(_app).run(timeout=30)
    assert any("start from every time" in i.value for i in at.info)
    _button(at, "guided_welcome_ok").click().run(timeout=30)
    assert not any("start from every time" in i.value for i in at.info)
    at.run(timeout=30)
    assert not any(b.key == "guided_welcome_ok" for b in at.button)
