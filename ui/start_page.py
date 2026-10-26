"""The first page on an empty registry (spec 16, 2): be guided, or do it
yourself. Not in the menu; from the first movement on the app opens on the
Home and this page sends there."""
from __future__ import annotations

import streamlit as st

from services.transaction_service import TransactionService
from ui.i18n import t
from ui.widgets.immersive import GUIDED_PAGE, GUIDED_RETURN, GUIDED_STEP

START_PAGE = "start"


def landing_page(engine) -> str:
    """Where the app opens (spec 19, 11bis): the Home, the end of every day's
    work, or this page while the registry is empty.

    Args:
        engine: the SQLAlchemy engine of the user's database.
    """
    return "home" if TransactionService(engine).has_transactions() else START_PAGE


def render_start_page(engine) -> None:
    if TransactionService(engine).has_transactions():
        rerun = st.session_state.get("page") == START_PAGE
        st.session_state["page"] = "home"
        if rerun:                    # once: the router then draws the Home
            st.rerun()
        return
    st.header(t("start.title"))
    st.write(t("start.intro"))
    c_guided, c_self = st.columns(2, gap="large")
    with c_guided.container(border=True):
        st.subheader(t("start.guided.title"))
        st.write(t("start.guided.text"))
        if st.button(t("start.guided.btn"), key="start_guided", type="primary", width="stretch"):
            st.session_state[GUIDED_RETURN] = START_PAGE
            st.session_state.pop(GUIDED_STEP, None)
            st.session_state["page"] = GUIDED_PAGE
            st.rerun()
    with c_self.container(border=True):
        st.subheader(t("start.self.title"))
        st.write(t("start.self.text"))
        if st.button(t("start.self.btn"), key="start_self", width="stretch"):
            st.session_state["page"] = "import"
            st.rerun()
