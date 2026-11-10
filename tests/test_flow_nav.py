"""The navigation bar under a flow step (UX spec, section 0).

The guided shell runs with a stub router: each step draws a marker and, when
the test asks for it, hands the bar choices not saved or an action of its
own. Saving writes a marker in the session, so the test sees whether the bar
saved before moving.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _app():
    import streamlit as st
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from db.models import create_tables
    from services.settings_service import SettingsService
    from ui.guided_page import render_guided_page
    from ui.i18n import set_language
    from ui.widgets import flow_nav

    set_language("en")
    if "_engine" not in st.session_state:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        create_tables(eng)
        SettingsService(eng).create_account("BancaX", "")
        SettingsService(eng).set("guided_welcome_seen", "true")
        st.session_state["_engine"] = eng
        st.session_state["page"] = "guided"
        st.session_state["_saved"] = []

    def _step(step: str, _engine) -> None:
        st.write(f"STEP:{step}")
        if st.session_state.get("_test_own_action"):
            flow_nav.own_action()
        pending = st.session_state.get("_test_pending", 0)
        if pending:
            flow_nav.offer_save(pending, lambda: st.session_state["_saved"].append(step))
        unsavable = st.session_state.get("_test_unsavable", 0)
        if unsavable:
            flow_nav.offer_save(unsavable, None)

    if st.session_state["page"] == "guided":
        render_guided_page(st.session_state["_engine"], _step)
    else:
        st.write(f"PAGE:{st.session_state['page']}")


def _texts(at):
    return [m.value for m in at.markdown]


def _button(at, key):
    return next(b for b in at.button if b.key == key)


def _start(**state):
    at = AppTest.from_function(_app)
    at.run(timeout=30)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run(timeout=30)


def test_the_bar_comes_after_the_step():
    at = _start()
    keys = [b.key for b in at.button]
    assert keys[-3:] == ["flow_back", "flow_leave", "flow_next"]


def test_without_pending_choices_next_is_the_primary():
    at = _start()
    nxt = _button(at, "flow_next")
    assert nxt.label == "Next"
    assert nxt.proto.type == "primary"


def test_pending_choices_make_save_and_go_on_the_primary_and_it_saves_first():
    at = _start(_test_pending=2)
    nxt = _button(at, "flow_next")
    assert nxt.label == "Save and go on"
    assert any("Choices to save: 2" in c.value for c in at.caption)
    nxt.click().run(timeout=30)
    assert at.session_state["_saved"] == ["checklist"]
    assert "STEP:import" in _texts(at)


def test_an_own_action_of_the_step_leaves_the_bar_without_a_primary():
    at = _start(_test_own_action=True)
    assert _button(at, "flow_next").proto.type == "secondary"


def test_back_saves_without_asking():
    at = _start()
    _button(at, "flow_next").click().run(timeout=30)
    at.session_state["_test_pending"] = 1
    at.run(timeout=30)
    _button(at, "flow_back").click().run(timeout=30)
    assert at.session_state["_saved"] == ["import"]
    assert "STEP:checklist" in _texts(at)


def test_leaving_without_pending_choices_asks_nothing():
    at = _start()
    _button(at, "flow_leave").click().run(timeout=30)
    assert "PAGE:home" in _texts(at)


def test_leaving_with_pending_choices_asks_first():
    at = _start(_test_pending=3)
    _button(at, "flow_leave").click().run(timeout=30)
    assert "STEP:checklist" in _texts(at)                 # still in the flow
    assert any("Choices not saved: 3." in m for m in _texts(at))
    assert {"flow_leave_save", "flow_leave_drop", "flow_leave_stay"} <= {b.key for b in at.button}


def test_leaving_and_not_saving_drops_the_choices():
    at = _start(_test_pending=1)
    _button(at, "flow_leave").click().run(timeout=30)
    _button(at, "flow_leave_drop").click().run(timeout=30)
    assert "PAGE:home" in _texts(at)
    assert at.session_state["_saved"] == []


def test_leaving_and_saving_keeps_the_choices():
    at = _start(_test_pending=1)
    _button(at, "flow_leave").click().run(timeout=30)
    _button(at, "flow_leave_save").click().run(timeout=30)
    assert "PAGE:home" in _texts(at)
    assert at.session_state["_saved"] == ["checklist"]


def test_the_page_height_comes_from_the_window_or_falls_back(tmp_path, monkeypatch):
    from ui.widgets import flow_nav

    viewport = tmp_path / "viewport_height"
    monkeypatch.setattr(flow_nav, "_viewport_file", lambda: viewport)
    assert flow_nav.viewport_height() == flow_nav.FALLBACK_VIEWPORT_PX
    viewport.write_text("1000")
    assert flow_nav.viewport_height() == 1000
    assert flow_nav.grid_height(400) == 600
    assert flow_nav.grid_height(990) == flow_nav.MIN_GRID_PX
    viewport.write_text("garbage")
    assert flow_nav.viewport_height() == flow_nav.FALLBACK_VIEWPORT_PX


def test_choices_only_the_step_can_save_are_counted_but_not_saved_by_next():
    at = _start(_test_unsavable=2)
    assert any("Save them with the button of the step" in c.value for c in at.caption)
    assert _button(at, "flow_next").label == "Next"


def test_back_with_choices_only_the_step_can_save_asks_without_a_save_button():
    at = _start()
    _button(at, "flow_next").click().run(timeout=30)
    at.session_state["_test_unsavable"] = 1
    at.run(timeout=30)
    _button(at, "flow_back").click().run(timeout=30)
    keys = {b.key for b in at.button}
    assert "flow_leave_drop" in keys and "flow_leave_save" not in keys
    _button(at, "flow_leave_drop").click().run(timeout=30)
    assert "STEP:checklist" in _texts(at)


def test_cancel_keeps_the_user_in_the_step():
    at = _start(_test_pending=1)
    _button(at, "flow_leave").click().run(timeout=30)
    _button(at, "flow_leave_stay").click().run(timeout=30)
    assert "STEP:checklist" in _texts(at)
    assert not any(b.key == "flow_leave_stay" for b in at.button)
    assert at.session_state["_saved"] == []


def _bar_only(next_label, reason):
    import streamlit as st

    from ui.i18n import set_language
    from ui.widgets import flow_nav

    set_language("en")
    st.session_state.setdefault("page", "check")
    flow_nav.begin_step()
    if st.session_state.get("_test_pending"):
        flow_nav.offer_save(1, lambda: None)
    flow_nav.render_bar(back_label="Back", on_back=None, leave_label="Leave", on_leave=lambda: None,
                        next_label=next_label, on_next=(lambda: None) if next_label else None,
                        next_disabled_reason=reason)


def test_without_a_next_of_its_own_the_bar_only_counts_the_choices():
    at = AppTest.from_function(_bar_only, args=(None, ""))
    at.session_state["_test_pending"] = True
    at.run(timeout=30)
    assert not any(b.key == "flow_next" for b in at.button)
    assert any("Choices to save: 1" in c.value for c in at.caption)


def test_next_disabled_says_why():
    at = AppTest.from_function(_bar_only, args=("Next", "Nothing to check from this date.")).run(timeout=30)
    assert _button(at, "flow_next").disabled
    assert any("Nothing to check" in c.value for c in at.caption)
