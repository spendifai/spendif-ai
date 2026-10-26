import os

import streamlit as st

from ui.i18n import t
from ui.components.update_checker import render_update_warning
from ui.widgets.immersive import GUIDED_PAGE, GUIDED_RETURN, GUIDED_STEP

# Navigation entries: (i18n_key_suffix, page_key).
CHECK_PAGE = "check"
_NAV_KEYS = [
    ("home",            "home"),
    ("import",          "import"),
    ("history",         "history"),
    ("ledger",          "ledger"),
    ("expense_confirm", "expense_confirm"),   # a receipt in hand (spec 32)
    ("bulk_edit",       "bulk_edit"),
    ("analytics",       "analytics"),
    ("report",          "report"),
    ("budget",          "budget"),
    ("budget_vs_actual","budget_vs_actual"),
    ("review",          "review"),
    ("rules",           "rules"),
    ("counterparts",    "counterparts"),
    ("taxonomy",        "taxonomy"),
    ("llm_models",      "llm_models"),
    ("settings",        "settings"),
    ("diagnostics",     "diagnostics"),
    ("intermediaries",  "intermediaries"),   # for experts (spec 20)
    ("checklist",       "checklist"),
    ("chat",            "chat"),
]

# The menu is the same for everyone (spec 19, 11bis). On top, under the
# guided flow, the day's gestures with the Home last, where the app lands;
# every other page in one of two closed groups: the money in depth, and the
# rules and settings of the app itself.
TOP_PAGES = [CHECK_PAGE, "expense_confirm", "chat", "home"]
MENU_GROUPS = [
    ("insights", ["ledger", "analytics", "report", "budget", "budget_vs_actual", "counterparts"]),
    ("setup",    ["import", "history", "bulk_edit", "review", "checklist", "rules", "taxonomy",
                  "intermediaries", "llm_models", "settings", "diagnostics"]),
]

NAV_PAGES = [CHECK_PAGE] + [key for _, key in _NAV_KEYS]
_NAV_SUFFIX = {key: suffix for suffix, key in _NAV_KEYS}


def _go(key: str, llm_running: bool) -> None:
    """Navigate to *key*, or ask first while the model is running."""
    if llm_running and key != st.session_state["page"]:
        st.session_state["_pending_nav_target"] = key
    elif key == CHECK_PAGE:
        st.session_state["_check_return"] = st.session_state["page"]
        st.session_state.pop("_check_phase", None)
        st.session_state["page"] = CHECK_PAGE
    else:
        st.session_state["page"] = key
    st.rerun()


def _nav_button(key: str, where) -> None:
    suffix = _NAV_SUFFIX.get(key, key)
    is_active = st.session_state["page"] == key
    if where.button(t(f"nav.{suffix}"), key=f"nav_{key}", width="stretch",
                    type="primary" if is_active else "secondary", help=t(f"nav.{suffix}.desc")):
        _go(key, st.session_state.get("llm_in_progress", False))


def render_sidebar() -> str:
    """Render the sidebar and return the selected page key."""
    render_update_warning()
    st.sidebar.title(t("sidebar.title"))

    if "page" not in st.session_state:
        st.session_state["page"] = "import"

    llm_running = st.session_state.get("llm_in_progress", False)

    # Navigation guard: warn user when an LLM process is active
    if llm_running:
        st.sidebar.warning(t("sidebar.llm_warning"))

    # Confirmation gate: if user clicked a nav button while LLM was running,
    # show confirm/cancel instead of navigating immediately.
    pending_nav = st.session_state.pop("_pending_nav_target", None)
    if pending_nav and llm_running:
        st.sidebar.error(t("sidebar.nav_confirm_msg"))
        col1, col2 = st.sidebar.columns(2)
        with col1:
            if st.button(t("common.confirm"), key="nav_confirm_interrupt", type="primary"):
                st.session_state["llm_in_progress"] = False
                st.session_state["page"] = pending_nav
                st.rerun()
        with col2:
            if st.button(t("sidebar.nav_stay"), key="nav_cancel_interrupt"):
                st.rerun()
        # While confirmation is pending, still show nav but don't process clicks
        _render_build_info()
        return st.session_state["page"]

    # Guided workflow first, apart from the rest: it reopens where it was left.
    if st.sidebar.button(
        t("nav.guided"), key="nav_guided", width="stretch", type="primary",
        help=t("nav.guided.desc"),
    ):
        if llm_running:
            st.session_state["_pending_nav_target"] = GUIDED_PAGE
        else:
            st.session_state[GUIDED_RETURN] = st.session_state["page"]
            st.session_state.pop(GUIDED_STEP, None)
            st.session_state["page"] = GUIDED_PAGE
        st.rerun()
    for key in TOP_PAGES:
        _nav_button(key, st.sidebar)
    st.sidebar.divider()

    # The two groups, closed; the one holding the page on screen opens by
    # itself, so the user always sees where they are.
    for group, keys in MENU_GROUPS:
        with st.sidebar.expander(t(f"nav.group.{group}"), expanded=st.session_state["page"] in keys):
            for key in keys:
                _nav_button(key, st)

    # Dev-only entry (AI-193): hardcoded label, shown only when SPENDIFAI_DEV_MODE=1.
    if os.getenv("SPENDIFAI_DEV_MODE") == "1":
        is_active = st.session_state["page"] == "debugger"
        if st.sidebar.button(
            "🔬 Debugger",
            key="nav_debugger",
            width="stretch",
            type="primary" if is_active else "secondary",
            help="Dev tool: trace pipeline su un campione, nessuna scrittura DB",
        ):
            st.session_state["page"] = "debugger"
            st.rerun()

    _render_build_info()
    return st.session_state["page"]


def _render_build_info() -> None:
    from services.app_info import get_build_info

    build_version, build_time = get_build_info()
    if build_time == "dev":
        return
    st.sidebar.markdown(
        f"<div style='text-align:center;color:#666;font-size:11px;margin-top:8px'>"
        f"v{build_version} · {build_time}</div>",
        unsafe_allow_html=True,
    )
