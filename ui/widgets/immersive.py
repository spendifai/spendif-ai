"""Immersive layout and navigation shared by the onboarding and the guided workflow.

Immersive = no sidebar, no header, no toolbar: only the current step is on
screen. The onboarding also narrows the column; the workflow keeps the full
width, because its steps are tables.
"""
from __future__ import annotations

import streamlit as st

# Session keys of the guided workflow. The step is mirrored in the DB by
# GuidedFlowService.save_step so it survives a restart.
GUIDED_PAGE = "guided"
GUIDED_STEP = "_guided_step"
GUIDED_RETURN = "_guided_return"


def hide_chrome(narrow: bool = True) -> None:
    """Hide sidebar, header and toolbar; with ``narrow``, also narrow the main column."""
    width = "max-width: 820px;" if narrow else ""
    st.markdown("""
        <style>
        [data-testid="stSidebar"] { display: none !important; }
        [data-testid="stHeader"] { display: none !important; }
        [data-testid="stToolbar"] { display: none !important; }
        section[data-testid="stSidebarUserContent"] { display: none !important; }
        .main .block-container {
            WIDTH
            padding-top: 2rem;
            padding-bottom: 4rem;
        }
        /* Streamlit injects a small "Manage app"/"Deploy" button: hide it too.
           Both the legacy class and the current testid (Streamlit renames often). */
        .stDeployButton, [data-testid="stAppDeployButton"] { display: none !important; }
        </style>
    """.replace("WIDTH", width), unsafe_allow_html=True)


def go_to(page: str) -> None:
    """Navigate to *page*, staying inside the guided workflow when active.

    A page button such as "Upload the file" on the Check List must move the
    workflow to its import step, not drop the user out of it.
    """
    from services.guided_flow_service import STEPS

    if st.session_state.get("page") == GUIDED_PAGE and page in STEPS:
        st.session_state[GUIDED_STEP] = page
    else:
        st.session_state["page"] = page
    st.rerun()
