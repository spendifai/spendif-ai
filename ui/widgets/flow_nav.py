"""The navigation bar at the bottom of a flow step (UX spec, section 0).

A flow (the guided workflow, the checking of the movements) draws the step
first and the bar after it, so the step can hand the bar what it holds: its
choices not saved yet and how to save them, or an action of its own still to
do. The bar then picks the one primary action of the moment:

1. the step's own action, drawn by the step: the bar has no primary;
2. choices not saved: "Save and go on";
3. otherwise Next, or Finish on the last step.

Back saves the pending choices without asking; leaving the flow with pending
choices asks first. A choice is never lost in silence.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()

# Session key of the step being drawn in this run.
_STEP_STATE = "_flow_step_state"
# Container key of the bar: Streamlit turns it into the CSS class st-key-<key>.
BAR_KEY = "flow_nav_bar"

# Height of the page when the app window did not report it: the minimum
# height of the desktop window (desktop/launcher.py, min_size).
FALLBACK_VIEWPORT_PX = 700
# A grid never shrinks below this, even in a very short window.
MIN_GRID_PX = 160


@dataclass
class PendingSave:
    """Choices of one part of a step, not saved yet.

    Attributes:
        count: how many choices wait to be saved, at least 1.
        save: writes them; None when the part cannot save on the bar's behalf
            (its save asks follow-up questions), so leaving must ask first.
    """

    count: int
    save: Callable[[], None] | None


@dataclass
class StepState:
    """What the step drawn in this run handed to the bar.

    Attributes:
        page: the page the flow runs in, so a later run on another page does
            not read a stale state left by a run stopped by st.rerun().
        own_action: the step has an action of its own still to do (import the
            staged files, answer the direction of the amounts).
        pending: the parts of the step with choices not saved.
    """

    page: str
    own_action: bool = False
    pending: list[PendingSave] = field(default_factory=list)


def begin_step() -> None:
    """Start collecting what the step drawn next hands to the bar."""
    st.session_state[_STEP_STATE] = StepState(page=st.session_state.get("page", ""))


def _state() -> StepState | None:
    state = st.session_state.get(_STEP_STATE)
    if isinstance(state, StepState) and state.page == st.session_state.get("page", ""):
        return state
    return None


def in_flow() -> bool:
    """Whether the page is being drawn as a step of a flow, under the bar."""
    return _state() is not None


def own_action() -> None:
    """The step has an action of its own to do first: the bar has no primary."""
    state = _state()
    if state is not None:
        state.own_action = True


def offer_save(count: int, save: Callable[[], None] | None) -> None:
    """Hand the bar choices not saved yet.

    Args:
        count: how many choices wait; 0 hands nothing.
        save: writes them without drawing anything or rerunning; None when
            only the part's own button can save them.
    """
    state = _state()
    if state is not None and count > 0:
        state.pending.append(PendingSave(count, save))


def pending_count() -> int:
    """How many choices of the step wait to be saved."""
    state = _state()
    return sum(p.count for p in state.pending) if state else 0


def _can_save_all(state: StepState) -> bool:
    return all(p.save is not None for p in state.pending)


def _save_all(state: StepState) -> None:
    for part in state.pending:
        if part.save is not None:
            part.save()
    logger.info("flow_nav: saved %d pending choices", sum(p.count for p in state.pending))
    state.pending.clear()


# Colors of the bar and its buttons, per theme (UX spec, section 0.1), from the
# palette of the UI design system: the bar stands out from the page and the
# buttons from the bar, never the color of the bar's background.
_BAR_COLORS = {
    False: {"bar": "#F8F9FA", "bar_border": "#E0E0E0",
            "secondary_bg": "#FFFFFF", "secondary_border": "#2980B9", "secondary_text": "#1E3C64",
            "primary_bg": "#1E3C64", "primary_text": "#FFFFFF", "primary_hover": "#2980B9"},
    True: {"bar": "#2D2D2D", "bar_border": "#3C4043",
           "secondary_bg": "#1E1E1E", "secondary_border": "#7AB5DD", "secondary_text": "#7AB5DD",
           "primary_bg": "#5B89C9", "primary_text": "#1E1E1E", "primary_hover": "#7AB5DD"},
}


def _bar_style(dark: bool) -> str:
    """The style block of the bar, kept apart from Streamlit so a test can read it.

    Args:
        dark: True for the dark theme, False for the light one.
    """
    colors = _BAR_COLORS[dark]
    bar = f".st-key-{BAR_KEY}"
    return f"""
        <style>
        {bar} {{
            position: fixed; left: 0; right: 0; bottom: 0; z-index: 999;
            background: {colors["bar"]};
            border-top: 1px solid {colors["bar_border"]};
            padding: 0.6rem 2rem 0.8rem 2rem;
        }}
        {bar} [data-testid="stBaseButton-secondary"]:not(:disabled) {{
            background: {colors["secondary_bg"]};
            border: 1px solid {colors["secondary_border"]};
            color: {colors["secondary_text"]};
        }}
        {bar} [data-testid="stBaseButton-primary"]:not(:disabled) {{
            background: {colors["primary_bg"]};
            border: 1px solid {colors["primary_bg"]};
            color: {colors["primary_text"]};
        }}
        {bar} [data-testid="stBaseButton-primary"]:hover:not(:disabled) {{
            background: {colors["primary_hover"]};
            border-color: {colors["primary_hover"]};
        }}
        .main .block-container, [data-testid="stMainBlockContainer"] {{
            padding-bottom: 6rem !important;
        }}
        </style>
    """


def _bar_css() -> None:
    """The bar stays at the bottom of the window, over the content."""
    dark = getattr(st.context.theme, "type", None) == "dark"
    st.markdown(_bar_style(dark), unsafe_allow_html=True)


# Session key: the bar is asking whether to save before going where it was
# asked to ("leave" or "back"). Kept in the session so the question survives
# the reruns its own buttons cause.
_ASKING = "_flow_asking"


@st.dialog(" ", dismissible=False)
def _confirm(count: int, can_save: bool, go: Callable[[], None]) -> None:
    """Choices not saved before leaving: save, drop or stay.

    Args:
        count: how many choices wait.
        can_save: the bar can save them; when False only drop or stay.
        go: where the user asked to go, called after save or drop.
    """
    st.markdown(f"**{t('flow.leave_title', n=count)}**")
    if not can_save:
        st.caption(t("flow.leave_cannot_save"))
    col_save, col_drop, col_stay = st.columns(3)
    if can_save and col_save.button(t("flow.leave_save"), key="flow_leave_save", width="stretch"):
        st.session_state.pop(_ASKING, None)
        state = _state()
        if state is not None:
            _save_all(state)
        go()
    if col_drop.button(t("flow.leave_drop"), key="flow_leave_drop", width="stretch"):
        st.session_state.pop(_ASKING, None)
        go()
    # Streamlit cannot move the focus: Cancel is the last button, as in the
    # operating system dialogs (UX spec, section 5).
    if col_stay.button(t("flow.leave_stay"), key="flow_leave_stay", width="stretch"):
        st.session_state.pop(_ASKING, None)
        st.rerun()


def render_bar(
    *,
    back_label: str,
    on_back: Callable[[], None] | None,
    leave_label: str,
    on_leave: Callable[[], None],
    next_label: str | None,
    on_next: Callable[[], None] | None,
    next_disabled_reason: str = "",
    save_label: str = "",
) -> None:
    """Draw the bar after the step and act on its buttons.

    Args:
        back_label: label of the secondary action that goes one step back.
        on_back: moves one step back; None on the first step (disabled).
        leave_label: label of the secondary action that leaves the flow.
        on_leave: leaves the flow; called after the confirmation when choices
            wait.
        next_label: label of the primary action without pending choices
            (Next, Finish); None when the phase has no Next of its own.
        on_next: moves on; None together with next_label.
        next_disabled_reason: when not empty Next is disabled and the reason
            is written next to it (UX-34).
        save_label: label of the primary with pending choices; empty means
            "Save and go on".
    """
    state = _state() or StepState(page=st.session_state.get("page", ""))
    count = sum(p.count for p in state.pending)
    can_save = _can_save_all(state)
    _bar_css()
    with st.container(key=BAR_KEY):
        col_back, col_leave, col_info, col_primary = st.columns([1, 1.4, 2.4, 1.6], vertical_alignment="center")
        if col_back.button(back_label, key="flow_back", disabled=on_back is None, width="stretch"):
            if count and not can_save:
                st.session_state[_ASKING] = "back"
            else:
                _save_all(state)
                on_back()
        if col_leave.button(leave_label, key="flow_leave", width="stretch"):
            if count:
                st.session_state[_ASKING] = "leave"
            else:
                on_leave()
        asking = st.session_state.get(_ASKING)
        if asking and count:
            if asking == "back" and on_back is not None:
                _confirm(count, False, on_back)
            else:
                _confirm(count, can_save, on_leave)
            return
        st.session_state.pop(_ASKING, None)
        if next_label is None:
            if count:
                col_info.caption(t("flow.pending", n=count))
            return
        primary = not state.own_action
        if count and can_save:
            col_info.caption(t("flow.pending", n=count))
            if col_primary.button(save_label or t("flow.save_and_next"), key="flow_next", type="primary" if primary else "secondary",
                                  width="stretch"):
                _save_all(state)
                on_next()
            return
        if count:
            col_info.caption(t("flow.pending_own_save", n=count))
        elif next_disabled_reason:
            col_info.caption(next_disabled_reason)
        if col_primary.button(next_label, key="flow_next", type="primary" if primary else "secondary",
                              disabled=bool(next_disabled_reason), width="stretch"):
            on_next()


def _viewport_file() -> Path:
    """Where the desktop window writes the height of the page, in CSS pixels."""
    return Path.home() / ".spendifai" / "viewport_height"


def viewport_height() -> int:
    """The height of the page in CSS pixels, as the app window last reported it.

    Streamlit does not know the size of the window; the desktop launcher asks
    the page and writes it down at every load and resize. In a plain browser
    (Docker) nobody writes it, and the minimum window height stands in.
    """
    try:
        value = int(_viewport_file().read_text().strip())
    except (OSError, ValueError):
        return FALLBACK_VIEWPORT_PX
    return value if value >= 300 else FALLBACK_VIEWPORT_PX


def grid_height(reserved_px: int) -> int:
    """Height of a step's grid so the whole step fits the window.

    Args:
        reserved_px: what the rest of the step takes, header, text, controls
            and the bar, in CSS pixels.
    """
    return max(MIN_GRID_PX, viewport_height() - reserved_px)
