"""Checking movements one account at a time (spec 31).

Step 0, the list: for every account the last checked movement and how
many are left; the account furthest behind is proposed with the day to
start from, both changeable. Step 1, the grid: the Registry grid on the
unchecked movements of that account from that day, oldest first; every
save refreshes it with the next ones. Step 2: the account's transfers
without the other side, then back to the list.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from services.checking_service import CheckingService, proposed
from services.settings_service import SettingsService
from services.transfer_service import TransferService
from support.formatting import format_amount_display, format_date_display
from ui.i18n import t
from ui.registry_page import render_check_grid
from ui.transfers_page import render_singles
from ui.widgets import flow_nav
from ui.widgets.immersive import hide_chrome

CHECK_PHASE = "_check_phase"
CHECK_RETURN = "_check_return"
CHECK_ACCOUNT = "_check_account"
CHECK_SINCE = "_check_since"
_LIST, _GRID, _TRANSFERS = "list", "grid", "transfers"


def _go(phase: str) -> None:
    st.session_state[CHECK_PHASE] = phase
    st.rerun()


def _render_list(svc: CheckingService, date_fmt: str) -> tuple[str, date] | None:
    """The accounts and where each stands; the account and day picked, or None
    when there is nothing to check (Next disabled)."""
    st.markdown(t("check.intro"))
    accounts = svc.accounts()
    if not accounts:
        st.info(t("check.no_accounts"))
        return None
    st.dataframe(
        [{
            t("check.col.account"): a.account,
            t("check.col.last_checked"): (format_date_display(a.last_checked.isoformat(), date_fmt)
                                          if a.last_checked else t("check.never")),
            t("check.col.first_unchecked"): (format_date_display(a.first_unchecked.isoformat(), date_fmt)
                                             if a.first_unchecked else ""),
            t("check.col.to_check"): a.to_check,
        } for a in accounts],
        hide_index=True, width="stretch",
    )
    best = proposed(accounts)
    if best is None:
        st.success(t("check.all_even"))
        return None
    names = [a.account for a in accounts]
    account = st.selectbox(t("check.account"), names, index=names.index(best.account), key="check_pick_account")
    chosen = next(a for a in accounts if a.account == account)
    since_key = f"check_pick_since_{account}"
    st.session_state.setdefault(since_key, chosen.start or date.today())
    since = st.date_input(t("check.since"), key=since_key, format="DD/MM/YYYY")
    return (account, since) if svc.remaining(account, since) else None


def _render_grid_phase(engine, svc: CheckingService, date_fmt: str) -> None:
    account = st.session_state.get(CHECK_ACCOUNT)
    since = st.session_state.get(CHECK_SINCE)
    if not account or not since:
        _go(_LIST)
    txs = svc.unchecked(account, since)
    if not txs:
        _go(_TRANSFERS)
    c_head, c_raw = st.columns([5, 1])
    c_head.markdown(t("check.grid_header", account=account,
                      date=format_date_display(since.isoformat(), date_fmt), n=len(txs)))
    # The bank's text next to the description, as in the Registry.
    show_raw = c_raw.toggle(t("ledger.filter.show_raw"), key="check_show_raw",
                            help=t("ledger.col.raw_help"))
    render_check_grid(engine, txs, show_raw)


def _render_transfers_phase(engine, svc: CheckingService, date_fmt: str, fmt) -> None:
    account = st.session_state.get(CHECK_ACCOUNT)
    since = st.session_state.get(CHECK_SINCE)
    singles = svc.one_sided_transfers(account, since) if account and since else []
    if not singles:
        st.toast(t("check.account_done", account=account or ""))
        _go(_LIST)
    st.markdown(t("check.transfers_intro", account=account))
    render_singles(TransferService(engine), singles, date_fmt, fmt)


def _leave() -> None:
    st.session_state.pop(CHECK_PHASE, None)
    st.session_state["page"] = st.session_state.pop(CHECK_RETURN, None) or "home"
    st.rerun()


def _start(account: str, since: date) -> None:
    st.session_state[CHECK_ACCOUNT] = account
    st.session_state[CHECK_SINCE] = since
    _go(_GRID)


def render_checking_page(engine) -> None:
    """A flow of its own (spec 31): full screen, entered from its menu
    entry, left with its own button; the workflow's steps never lead here.
    The phase first, the bar after it (UX spec 2)."""
    hide_chrome(narrow=False)
    st.header(t("check.title"))
    settings = SettingsService(engine).get_all()
    date_fmt = settings.get("date_display_format", "%d/%m/%Y")
    dec = settings.get("amount_decimal_sep", ",")
    thou = settings.get("amount_thousands_sep", ".")

    def fmt(v: float) -> str:
        return format_amount_display(v, decimal_sep=dec, thousands_sep=thou)

    svc = CheckingService(engine)
    phase = st.session_state.get(CHECK_PHASE, _LIST)
    flow_nav.begin_step()
    back = {"back_label": t("check.back_to_list"), "leave_label": t("check.exit"), "on_leave": _leave}
    if phase == _GRID:
        _render_grid_phase(engine, svc, date_fmt)
        # The primary is "All the N shown are right", in the grid.
        flow_nav.render_bar(**back, on_back=lambda: _go(_LIST), next_label=None, on_next=None)
    elif phase == _TRANSFERS:
        _render_transfers_phase(engine, svc, date_fmt, fmt)
        flow_nav.render_bar(**back, on_back=lambda: _go(_LIST), next_label=t("check.save_back"),
                            on_next=lambda: _go(_LIST), save_label=t("check.save_back"))
    else:
        picked = _render_list(svc, date_fmt)
        flow_nav.render_bar(**back, on_back=None, next_label=t("check.next"),
                            on_next=(lambda: _start(*picked)) if picked else None,
                            next_disabled_reason="" if picked else t("check.nothing_to_check"))
