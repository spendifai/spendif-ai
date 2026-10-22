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

CHECK_PHASE = "_check_phase"
CHECK_ACCOUNT = "_check_account"
CHECK_SINCE = "_check_since"
_LIST, _GRID, _TRANSFERS = "list", "grid", "transfers"


def _go(phase: str) -> None:
    st.session_state[CHECK_PHASE] = phase
    st.rerun()


def _render_list(svc: CheckingService, date_fmt: str) -> None:
    st.markdown(t("check.intro"))
    accounts = svc.accounts()
    if not accounts:
        st.info(t("check.no_accounts"))
        return
    st.dataframe(
        [{
            t("check.col.account"): a.account,
            t("check.col.last_checked"): (format_date_display(a.last_checked.isoformat(), date_fmt)
                                          if a.last_checked else t("check.never")),
            t("check.col.to_check"): a.to_check,
        } for a in accounts],
        hide_index=True, width="stretch",
    )
    best = proposed(accounts)
    if best is None:
        st.success(t("check.all_even"))
        return
    names = [a.account for a in accounts]
    account = st.selectbox(t("check.account"), names, index=names.index(best.account), key="check_pick_account")
    chosen = next(a for a in accounts if a.account == account)
    since_key = f"check_pick_since_{account}"
    st.session_state.setdefault(since_key, chosen.start)
    since = st.date_input(t("check.since"), key=since_key, format="DD/MM/YYYY")
    if chosen.before and since >= chosen.start:
        st.caption(t("check.gap", n=chosen.before,
                     date=format_date_display(chosen.start.isoformat(), date_fmt)))
        first = svc.unchecked(account, chosen.first)
        first_day = date.fromisoformat(str(first[0].date)[:10]) if first else chosen.first
        # A callback: the date widget above is already drawn in this run.
        st.button(t("check.gap_start"), key="check_gap_start",
                  on_click=lambda: st.session_state.__setitem__(since_key, first_day))
    if st.button(t("check.next"), type="primary", key="check_next",
                 disabled=svc.remaining(account, since) == 0):
        st.session_state[CHECK_ACCOUNT] = account
        st.session_state[CHECK_SINCE] = since
        _go(_GRID)


def _render_grid_phase(engine, svc: CheckingService, date_fmt: str) -> None:
    account = st.session_state.get(CHECK_ACCOUNT)
    since = st.session_state.get(CHECK_SINCE)
    if not account or not since:
        _go(_LIST)
    txs = svc.unchecked(account, since)
    if not txs:
        _go(_TRANSFERS)
    c_head, c_back = st.columns([4, 1])
    c_head.markdown(t("check.grid_header", account=account,
                      date=format_date_display(since.isoformat(), date_fmt), n=len(txs)))
    if c_back.button(t("check.back_to_list"), key="check_back_grid"):
        _go(_LIST)
    render_check_grid(engine, txs)


def _render_transfers_phase(engine, svc: CheckingService, date_fmt: str, fmt) -> None:
    account = st.session_state.get(CHECK_ACCOUNT)
    since = st.session_state.get(CHECK_SINCE)
    singles = svc.one_sided_transfers(account, since) if account and since else []
    if not singles:
        st.toast(t("check.account_done", account=account or ""))
        _go(_LIST)
    st.markdown(t("check.transfers_intro", account=account))
    render_singles(TransferService(engine), singles, date_fmt, fmt)
    if st.button(t("check.back_to_list"), key="check_back_transfers"):
        _go(_LIST)


def render_checking_page(engine) -> None:
    st.header(t("check.title"))
    settings = SettingsService(engine).get_all()
    date_fmt = settings.get("date_display_format", "%d/%m/%Y")
    dec = settings.get("amount_decimal_sep", ",")
    thou = settings.get("amount_thousands_sep", ".")

    def fmt(v: float) -> str:
        return format_amount_display(v, decimal_sep=dec, thousands_sep=thou)

    svc = CheckingService(engine)
    phase = st.session_state.get(CHECK_PHASE, _LIST)
    if phase == _GRID:
        _render_grid_phase(engine, svc, date_fmt)
    elif phase == _TRANSFERS:
        _render_transfers_phase(engine, svc, date_fmt, fmt)
    else:
        _render_list(svc, date_fmt)
