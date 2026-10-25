"""Confirming an expense from a receipt in hand (spec 32).

Chat turns: the user types, or dictates with the operating system's own
dictation (the text field fills by itself, no audio reaches Spendif.ai), and
the bot follows the fixed flow of spec 32, 4. The flow and its writes are in
ExpenseConfirmService; this page only shows the turns.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

import streamlit as st
import streamlit.components.v1 as components

from services.expense_confirm_service import ExpenseConfirmService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.i18n import get_language, t
from ui.widgets.written_rules import write_and_apply

logger = setup_logging()

_STATE = "_expense_state"
_CHAT = "_expense_chat"
_LIST = "pick_list"          # core.expense_confirm.PICK_LIST, without importing core
_SPOKEN = "_expense_spoken"  # the last reply read aloud, None with the voice off
_VOICE = "_expense_voice"
_SENT = "_expense_sent"           # the answers given, for the dictation's repeats
# The voice of the operating system for the user's language (spec 32, 3.2).
_VOICES = {"it": "it-IT", "en": "en-US", "fr": "fr-FR", "de": "de-DE", "es": "es-ES"}


def spoken_amount(v: Decimal) -> str:
    """An amount as people say it (spec 32, 3.2): "24 e 90", "90 centesimi",
    "24 euro", never "24 virgola 90"."""
    cents_total = int((abs(v) * 100).quantize(Decimal("1")))
    euros, cents = divmod(cents_total, 100)
    if euros == 0:
        return t("expense.spoken_cents", cents=cents)
    if cents == 0:
        return t("expense.spoken_euros", euros=euros)
    return t("expense.spoken_amount", euros=euros, cents=f"{cents:02d}")


def spoken_text(markdown: str) -> str:
    """A reply as it is read aloud: no markdown, list items as sentences."""
    text = markdown.replace("**", "").replace("*", "")
    lines = [re.sub(r"^\s*-\s+", "", line).strip() for line in text.splitlines()]
    sentences = [line.rstrip(".") for line in lines if line]
    out = ""
    for i, sentence in enumerate(sentences):
        out += sentence
        if i < len(sentences) - 1:
            out += " " if sentence.endswith(":") else ". "
    return out + ("." if text.rstrip().endswith(".") else "")


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

    def amount(v: Decimal, spoken: bool = False) -> str:
        if spoken:
            return spoken_amount(v)
        return format_amount_display(float(v), decimal_sep=dec, thousands_sep=thou)

    def day(d: date) -> str:
        month = t(f"common.month.{d.month}")
        if d.year == date.today().year:
            return t("expense.day", d=d.day, month=month)
        return t("expense.day_year", d=d.day, month=month, y=d.year)

    def cat(pair) -> str:
        c, s = pair
        return f"{c} / {s}" if s else c

    def items(values, spoken: bool) -> str:
        lines = []
        for i, v in enumerate(values, 1):
            if isinstance(v, tuple):
                lines.append(f"{i}) {cat(v)}")
            else:
                lines.append(t("expense.item", i=i, name=v.counterpart, day=day(v.day),
                               amount=amount(v.amount, spoken)))
        return "\n".join(f"- {line}" for line in lines)

    def value(k: str, v, spoken: bool):
        if k == "items":
            return items(v, spoken)
        if isinstance(v, Decimal):
            return amount(v, spoken)
        if isinstance(v, date):
            return day(v)
        if isinstance(v, tuple):
            return cat(v)
        return v

    def say(msgs, spoken: bool = False) -> str:
        """The reply as shown, or with *spoken* as it is read aloud."""
        parts = []
        for m in msgs:
            parts.append(t(m.key, **{k: value(k, v, spoken) for k, v in m.params.items()}))
            if m.note:
                parts.append(m.note)
        return "\n\n".join(parts)

    return say


def speak_script(text: str, lang: str) -> str:
    """Read *text* aloud with a voice made for this reply (spec 32, 3.2)."""
    return (
        "<script>const s = window.speechSynthesis; if (s) { s.cancel();"
        f" const u = new SpeechSynthesisUtterance({json.dumps(text)}); u.lang = {json.dumps(lang)};"
        " s.speak(u); }</script>"
    )


def _bot_says(say, msgs) -> None:
    st.session_state[_CHAT].append(("assistant", say(msgs)))
    voice_on = st.session_state.get("expense_speak", True)
    st.session_state[_SPOKEN] = spoken_text(say(msgs, spoken=True)) if voice_on else None


def _speak() -> None:
    """Read the last reply aloud with the speech synthesis of the operating
    system, inside the app window: nothing goes to the network (spec 32,
    3.2)."""
    text = st.session_state.get(_SPOKEN)
    if not text:
        return
    voice = _VOICES.get(get_language(), "it-IT")
    st.session_state[_VOICE] = voice
    components.html(speak_script(text, voice), height=0)


def _reset(svc: ExpenseConfirmService, say) -> None:
    msgs, state = svc.start()
    st.session_state[_STATE] = state
    st.session_state[_CHAT] = []
    st.session_state[_SENT] = []
    _bot_says(say, msgs)


def _answer(svc: ExpenseConfirmService, say, text: str) -> None:
    sent = st.session_state.setdefault(_SENT, [])
    # The answer is sent with Enter, dictated or typed. The dictation writes
    # again the answers already given (spec 32, 3.2); an answer given again
    # on its own ("si'", then "si'") stays as it is.
    text = svc.new_part(text, sent) or text
    sent.append(text)
    st.session_state[_CHAT].append(("user", text))
    with st.spinner(t("expense.thinking")):
        msgs, state = svc.reply(st.session_state[_STATE], text)
    st.session_state[_STATE] = state
    _bot_says(say, msgs)


def render_expense_confirm_page(engine) -> None:
    st.header(t("expense.title"))
    # Under the title, not beside it: beside it the top bar of the app hid them.
    c_intro, c_speak, c_restart = st.columns([4, 1, 1], vertical_alignment="center")
    c_intro.caption(t("expense.intro"))
    c_speak.toggle(t("expense.speak"), value=True, key="expense_speak", help=t("expense.speak_help"))
    say = _formatter(SettingsService(engine).get_all())
    svc = ExpenseConfirmService(engine, rule_writer=_rule_writer(engine))
    if c_restart.button(t("expense.restart"), key="expense_restart") or _STATE not in st.session_state:
        _reset(svc, say)

    for role, text in st.session_state[_CHAT]:
        with st.chat_message(role):
            st.markdown(text)
    if st.session_state.get("expense_speak", True):
        _speak()

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
