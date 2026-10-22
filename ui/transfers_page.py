"""Transfers: the workflow step after the mixed categories.

Money moved between the user's own accounts leaves one account and reaches
another: two movements, one per account, usually in different files. The
import puts them together when it can; this step shows what it found.

- Pairs to approve: the two sides with dates, accounts and how sure the
  pairing is, in words with its reason. The user says yes (a transfer) or no
  (kept apart and never proposed again). Sure pairs and pairs already
  answered are not shown again, only counted.
- One-sided transfers: the other account is not loaded. The user says where
  the money went, once per beneficiary: an account of mine not loaded yet, an
  account of mine I do not follow, or another person (then it is spending or
  income, also in the imports to come). Answered beneficiaries are not shown
  again.

Only the window the Check List asks about is shown; older items are counted.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from services.settings_service import SettingsService
from services.transfer_service import (
    DESTINATION_ANSWERS,
    PairRow,
    SingleRow,
    TransferService,
)
from support.formatting import format_amount_display, format_date_display
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()

def _days(n: int) -> str:
    if n == 0:
        return t("transfers.days.same")
    if n == 1:
        return t("transfers.days.one")
    return t("transfers.days.many", n=n)


def pair_reason(p: PairRow) -> str:
    """How sure the pairing is, in words, with what it rests on."""
    days = _days(p.days)
    if p.out_marked and p.in_marked:
        reason = t("transfers.reason.both", days=days)
    elif p.out_marked or p.in_marked:
        kind = t("transfers.kind.income") if p.out_marked else t("transfers.kind.expense")
        reason = t("transfers.reason.one", days=days, kind=kind)
    else:
        reason = t("transfers.reason.none", days=days)
    return f"{t(f'transfers.conf.{p.confidence}')}: {reason}"


def _render_pairs(svc: TransferService, pairs: list[PairRow], date_fmt: str, fmt) -> None:
    st.subheader(t("transfers.pairs_title"))
    st.caption(t("transfers.pairs_help"))
    yes, no = t("transfers.yes"), t("transfers.no")
    c = {k: t(f"transfers.col_{k}") for k in (
        "out_date", "out_account", "in_date", "in_account", "amount", "reliability", "pair_answer")}

    df = pd.DataFrame([{
        c["out_date"]: format_date_display(p.out_date.isoformat(), date_fmt),
        c["out_account"]: p.out_account,
        c["in_date"]: format_date_display(p.in_date.isoformat(), date_fmt),
        c["in_account"]: p.in_account,
        c["amount"]: fmt(float(p.amount)),
        c["reliability"]: pair_reason(p),
        c["pair_answer"]: "",
    } for p in pairs])
    edited = st.data_editor(
        df,
        column_config={
            **{c[k]: st.column_config.TextColumn(c[k], disabled=True)
               for k in ("out_date", "out_account", "in_date", "in_account", "amount")},
            c["reliability"]: st.column_config.TextColumn(c["reliability"], disabled=True, width="large"),
            c["pair_answer"]: st.column_config.SelectboxColumn(c["pair_answer"], options=["", yes, no]),
        },
        hide_index=True, num_rows="fixed", width="stretch", key="tr_pairs",
    )
    if st.button(t("transfers.save_btn"), type="primary", key="tr_pairs_save"):
        n = 0
        for idx, p in enumerate(pairs):
            value = edited.at[idx, c["pair_answer"]] or ""
            if not value:
                continue
            svc.decide(p.out_id, p.in_id, "yes" if value == yes else "no")
            n += 1
        logger.info(f"transfers_page: {n} pair decisions saved")
        st.session_state["_tr_saved"] = n
        st.rerun()


def render_singles(svc: TransferService, singles: list[SingleRow], date_fmt: str, fmt) -> None:
    st.subheader(t("transfers.singles_title"))
    st.markdown(t("transfers.singles_help"))
    labels = {a: t(f"transfers.answer.{a}") for a in DESTINATION_ANSWERS}
    by_label = {v: k for k, v in labels.items()}
    c = {k: t(f"transfers.col_{k}") for k in (
        "out_date", "out_account", "in_date", "in_account", "amount", "description", "single_answer")}

    def side(x: SingleRow, outgoing: bool, value: str) -> str:
        # The missing side stays empty: no label in place of the data.
        return value if (x.amount < 0) == outgoing else ""

    df = pd.DataFrame([{
        c["out_date"]: side(x, True, format_date_display(x.date.isoformat(), date_fmt)),
        c["out_account"]: side(x, True, x.account),
        c["in_date"]: side(x, False, format_date_display(x.date.isoformat(), date_fmt)),
        c["in_account"]: side(x, False, x.account),
        c["amount"]: fmt(float(abs(x.amount))),
        c["description"]: x.description,
        c["single_answer"]: "",
    } for x in singles])
    edited = st.data_editor(
        df,
        column_config={
            **{c[k]: st.column_config.TextColumn(c[k], disabled=True)
               for k in ("out_date", "out_account", "in_date", "in_account", "amount")},
            c["description"]: st.column_config.TextColumn(c["description"], disabled=True, width="large"),
            c["single_answer"]: st.column_config.SelectboxColumn(
                c["single_answer"], options=["", *labels.values()], width="medium"),
        },
        hide_index=True, num_rows="fixed", width="stretch", key="tr_singles",
    )
    if st.button(t("transfers.save_btn"), type="primary", key="tr_singles_save"):
        answers: dict[str, str] = {}
        for idx, x in enumerate(singles):
            value = by_label.get(edited.at[idx, c["single_answer"]] or "")
            if value:
                answers[x.key] = value      # one answer per beneficiary
        for key, value in answers.items():
            svc.answer(key, value)
        logger.info(f"transfers_page: {len(answers)} destination answers saved")
        st.session_state["_tr_saved"] = len(answers)
        st.rerun()


def render_transfers_page(engine) -> None:
    st.header(t("transfers.title"))

    svc = TransferService(engine)
    settings = SettingsService(engine).get_all()
    dec = settings.get("amount_decimal_sep", ",")
    thou = settings.get("amount_thousands_sep", ".")
    date_fmt = settings.get("date_display_format", "%d/%m/%Y")

    def fmt(v: float) -> str:
        return format_amount_display(v, decimal_sep=dec, thousands_sep=thou)

    saved = st.session_state.pop("_tr_saved", None)
    if saved:
        st.success(t("transfers.saved", n=saved))

    # A ledger imported before pairing existed is paired the first time here.
    svc.pair_all_once()
    view = svc.view()
    if not view.open_count:
        st.success(t("transfers.none"))
    else:
        st.markdown(t("transfers.intro"))
        st.caption(t("transfers.optional"))
        if view.pairs:
            _render_pairs(svc, view.pairs, date_fmt, fmt)
        if view.singles:
            render_singles(svc, view.singles, date_fmt, fmt)
    if view.settled:
        st.caption(t("transfers.settled", n=view.settled))
    if view.older:
        st.caption(t("transfers.older", n=view.older))
