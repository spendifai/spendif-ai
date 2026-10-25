"""Confirming an expense from a receipt in hand (spec 32).

Chat turns: the user types, or dictates with the operating system's own
dictation (the text field fills by itself, no audio reaches Spendif.ai), and
the bot follows the fixed flow of spec 32, 4. The flow and its writes are in
ExpenseConfirmService; this page only shows the turns.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from services.expense_confirm_service import ExpenseConfirmService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.written_rules import write_and_apply

logger = setup_logging()

_STATE = "_expense_state"
_CHAT = "_expense_chat"
_LIST = "pick_list"          # core.expense_confirm.PICK_LIST, without importing core


def _rule_writer(engine):
    """The rule after a yes to "Sempre cosi'?", written as the Registry
    writes it (spec 18, 5.2): the note says what the product wrote."""
    rule_svc, tx_svc = RuleService(engine), TransactionService(engine)

    def write(q: dict) -> str | None:
        pattern, match_type = rule_svc.write_rules([q["description"]])[q["description"]]
        n, note = write_and_apply(rule_svc, tx_svc, q["description"], pattern, match_type,
                                  q["category"], q["subcategory"], origin="expense_always")
        logger.info(f"expense_confirm_page: always-like-this rule written, applied to {n} movements")
        return note

    return write


def _formatter(settings: dict):
    dec = settings.get("amount_decimal_sep", ",")
    thou = settings.get("amount_thousands_sep", ".")

    def amount(v: Decimal) -> str:
        return format_amount_display(float(v), decimal_sep=dec, thousands_sep=thou)

    def day(d: date) -> str:
        month = t(f"common.month.{d.month}")
        if d.year == date.today().year:
            return t("expense.day", d=d.day, month=month)
        return t("expense.day_year", d=d.day, month=month, y=d.year)

    def cat(pair) -> str:
        c, s = pair
        return f"{c} / {s}" if s else c

    def items(values) -> str:
        lines = []
        for i, v in enumerate(values, 1):
            if isinstance(v, tuple):
                lines.append(f"{i}) {cat(v)}")
            else:
                lines.append(t("expense.item", i=i, name=v.counterpart, day=day(v.day), amount=amount(v.amount)))
        return "\n".join(f"- {line}" for line in lines)

    def value(k: str, v):
        if k == "items":
            return items(v)
        if isinstance(v, Decimal):
            return amount(v)
        if isinstance(v, date):
            return day(v)
        if isinstance(v, tuple):
            return cat(v)
        return v

    def say(msgs) -> str:
        parts = []
        for m in msgs:
            parts.append(t(m.key, **{k: value(k, v) for k, v in m.params.items()}))
            if m.note:
                parts.append(m.note)
        return "\n\n".join(parts)

    return say


def _reset(svc: ExpenseConfirmService, say) -> None:
    msgs, state = svc.start()
    st.session_state[_STATE] = state
    st.session_state[_CHAT] = [("assistant", say(msgs))]


def _answer(svc: ExpenseConfirmService, say, text: str) -> None:
    st.session_state[_CHAT].append(("user", text))
    with st.spinner(t("expense.thinking")):
        msgs, state = svc.reply(st.session_state[_STATE], text)
    st.session_state[_STATE] = state
    st.session_state[_CHAT].append(("assistant", say(msgs)))


def render_expense_confirm_page(engine) -> None:
    c_title, c_restart = st.columns([4, 1])
    c_title.header(t("expense.title"))
    st.caption(t("expense.intro"))
    say = _formatter(SettingsService(engine).get_all())
    svc = ExpenseConfirmService(engine, rule_writer=_rule_writer(engine))
    if c_restart.button(t("expense.restart"), key="expense_restart") or _STATE not in st.session_state:
        _reset(svc, say)

    for role, text in st.session_state[_CHAT]:
        with st.chat_message(role):
            st.markdown(text)

    state = st.session_state[_STATE]
    if state.step == _LIST:
        # The whole list after three rounds (spec 32, 4.3): a menu, not a
        # list to read aloud.
        labels = [f"{c} / {s}" if s else c for c, s in state.options]
        choice = st.selectbox(t("expense.list_label"), labels, key="expense_list")
        if st.button(t("expense.list_pick"), key="expense_list_pick", type="primary"):
            _answer(svc, say, choice)
            st.rerun()

    if text := st.chat_input(t("expense.placeholder"), key="expense_input"):
        _answer(svc, say, text)
        st.rerun()
