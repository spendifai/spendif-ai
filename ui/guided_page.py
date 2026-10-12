"""Guided workflow: the regular pages, one step at a time, without distractions.

Check List -> Import -> Counterparts -> Rules -> Report, in the immersive
layout of the onboarding. Each step renders the regular page under a header
with "Step n of N", Back, Next and Leave. Leaving saves the step; the menu
entry reopens the workflow where it was left, skipping the steps the data
has closed in the meantime (GuidedFlowService.resume).
"""
from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from services.guided_flow_service import STEPS, GuidedFlowService
from ui.i18n import t
from ui.widgets.immersive import GUIDED_RETURN, GUIDED_STEP, hide_chrome


def _leave(flow_svc: GuidedFlowService, step: str, target: str) -> None:
    flow_svc.save_step(step)
    st.session_state.pop(GUIDED_STEP, None)
    st.session_state["page"] = target
    st.session_state.pop(GUIDED_RETURN, None)
    st.rerun()


def _move(flow_svc: GuidedFlowService, step: str) -> None:
    flow_svc.save_step(step)
    st.session_state[GUIDED_STEP] = step
    if step == "report":
        # Land on the period just worked on, not on a range chosen earlier.
        st.session_state.pop("report_from", None)
        st.session_state.pop("report_to", None)
    st.rerun()


def render_guided_page(engine, render_step: Callable[[str, object], None]) -> None:
    """Render the current step through *render_step* (the app router)."""
    hide_chrome()
    flow_svc = GuidedFlowService(engine)

    step = st.session_state.get(GUIDED_STEP)
    if step not in STEPS:
        step = flow_svc.resume()
    if st.session_state.get(GUIDED_STEP) != step or step != flow_svc.saved_step():
        # Entering the workflow (menu, end of onboarding): remember the step.
        st.session_state[GUIDED_STEP] = step
        flow_svc.save_step(step)

    idx = STEPS.index(step)
    is_last = idx == len(STEPS) - 1

    st.caption(t("guided.step_of", n=idx + 1, total=len(STEPS), name=t(f"guided.step.{step}")))
    col_back, col_next, col_exit = st.columns([1, 1, 2])
    if col_back.button(t("guided.back"), key="guided_back", disabled=idx == 0, width="stretch"):
        _move(flow_svc, STEPS[idx - 1])
    if col_next.button(
        t("guided.finish") if is_last else t("guided.next"),
        key="guided_next", type="primary", width="stretch",
    ):
        if is_last:
            # Round complete: the next one starts from what to load, and the
            # user stays on the report, now with the sidebar back.
            _leave(flow_svc, STEPS[0], "report")
        _move(flow_svc, STEPS[idx + 1])
    if col_exit.button(t("guided.exit"), key="guided_exit"):
        _leave(flow_svc, step, st.session_state.get(GUIDED_RETURN) or "home")
    st.divider()

    render_step(step, engine)
