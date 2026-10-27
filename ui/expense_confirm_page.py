"""Confirming an expense from a receipt in hand (spec 32).

Chat turns: the user types, or dictates with the operating system's own
dictation (the text field fills by itself, no audio reaches Spendif.ai), and
the bot follows the fixed flow of spec 32, 4. The flow and its writes are in
ExpenseConfirmService; this page only shows the turns.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import streamlit as st
import streamlit.components.v1 as components

from services.expense_confirm_service import ExpenseConfirmService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from services.voice_service import VoiceService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.i18n import get_language, t
from ui.widgets.voice_turn import answer_for, turn_args, voice_turn
from ui.widgets.written_rules import write_and_apply

logger = setup_logging()

_STATE = "_expense_state"
_CHAT = "_expense_chat"
_LIST = "pick_list"          # core.expense_confirm.PICK_LIST, without importing core
_CLOSED = "closed"           # core.expense_confirm.CLOSED
_SPOKEN = "_expense_spoken"  # the last reply read aloud, None with the voice off
_VOICE = "_expense_voice"
_SENT = "_expense_sent"           # the answers given, for the dictation's repeats
_TURN = "_expense_turn"           # one number per bot reply, never reset: the recorder runs each turn once
_TAKEN = "_expense_taken"         # the last turn whose answer was taken
_VT_KEY = "expense_voice_turn"    # the app's own recorder; its answer is read from here
_CHAT_HEIGHT = 420                # px: the conversation scrolls inside, the page does not grow
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
            if m.note and not spoken:     # the detail stays on screen, the voice keeps the pace
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
    st.session_state[_TURN] = st.session_state.get(_TURN, 0) + 1
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


def _names_on_screen(state) -> list[str]:
    """Counterparts and categories the user may say next: given to the ears
    so they are heard as written."""
    names = [c.counterpart.title() for c in getattr(state, "candidates", ())]
    names += [f"{c} {s}".strip() for c, s in list(getattr(state, "nearest", ()))]
    return names


def _own_turn(voice_svc: VoiceService) -> None:
    """The bot's turn with the app's own recorder (EXPERIMENTAL): the reply
    read aloud, then the recorder listens for the answer."""
    text = st.session_state.get(_SPOKEN) if st.session_state.get("expense_speak", True) else None
    wav = None
    if text and get_language() == "it" and voice_svc.can_speak():
        try:
            t0 = time.perf_counter()
            wav = voice_svc.say(text)
            logger.info(f"expense_confirm_page: reply voiced in {(time.perf_counter() - t0) * 1000:.0f} ms")
        except Exception as exc:     # never lose the reply: the system voice reads it
            logger.warning(f"expense_confirm_page: own voice failed, system voice instead: {exc}")
    labels = {k: t(f"expense.vt.{k}") for k in
              ("start", "idle", "speaking", "listening", "hearing", "thinking", "denied")}
    labels["closed"] = t("expense.closed_hint")
    closed = st.session_state[_STATE].step == _CLOSED
    voice_turn(_VT_KEY, **turn_args(st.session_state.get(_TURN, 0), wav, None if wav else text,
                                    _VOICES.get(get_language(), "it-IT"), labels, last=closed))


def _take_answer(svc: ExpenseConfirmService, say, voice_svc: VoiceService) -> None:
    """The answer just recorded, if any (EXPERIMENTAL), taken before the chat
    is drawn so it shows in this same run: the app's own ears turn it into
    text, and the text goes the same way as a typed answer."""
    turn = st.session_state.get(_TURN, 0)
    wav = answer_for(st.session_state.get(_VT_KEY), turn, st.session_state.get(_TAKEN, -1))
    if wav is None:
        return
    st.session_state[_TAKEN] = turn
    t0 = time.perf_counter()
    heard = voice_svc.hear(wav, _names_on_screen(st.session_state[_STATE]))
    if not heard.text:
        st.toast(t("expense.not_heard"))
        st.session_state[_TURN] = turn + 1       # a turn that only listens
        st.session_state[_SPOKEN] = None
        return
    _answer(svc, say, heard.text, dictated=False)
    logger.info(f"expense_confirm_page: answer heard and replied in {(time.perf_counter() - t0) * 1000:.0f} ms")


def _answer(svc: ExpenseConfirmService, say, text: str, dictated: bool = True) -> None:
    sent = st.session_state.setdefault(_SENT, [])
    # The answer is sent with Enter, dictated or typed. The dictation writes
    # again the answers already given (spec 32, 3.2); an answer given again
    # on its own ("si'", then "si'") stays as it is.
    if dictated:       # our own ears hear one answer at a time: nothing to strip
        text = svc.new_part(text, sent) or text
    sent.append(text)
    st.session_state[_CHAT].append(("user", text))
    with st.spinner(t("expense.thinking")):
        msgs, state = svc.reply(st.session_state[_STATE], text)
    st.session_state[_STATE] = state
    _bot_says(say, msgs)


def _offer_listening(voice_svc: VoiceService, cfg: SettingsService) -> None:
    """The question that downloads the listening model (spec 32, 3): asked
    in full until the user answers no, then a small button asks again. The
    download runs in the background; the page follows it."""
    state = voice_svc.listening_download_state()
    if state["running"]:
        _follow_download()
        return
    if state["error"]:
        st.warning(t("expense.ears.failed"))
    if cfg.get("voice_listen_declined") == "true":
        if st.button(t("expense.ears.again"), key="expense_ears_again"):
            cfg.set("voice_listen_declined", "false")
            st.rerun()
        return
    with st.container(border=True):
        st.write(t("expense.ears.ask"))
        c_yes, c_no = st.columns(2)
        if c_yes.button(t("expense.ears.yes"), key="expense_ears_yes", type="primary", width="stretch"):
            voice_svc.start_listening_download()
            st.rerun()
        if c_no.button(t("expense.ears.no"), key="expense_ears_no", width="stretch"):
            cfg.set("voice_listen_declined", "true")
            st.rerun()


@st.fragment(run_every=1)
def _follow_download() -> None:
    """The download's progress, redrawn every second; when it ends the page
    runs again and finds the ears."""
    state = VoiceService.listening_download_state()
    if not state["running"]:
        st.rerun(scope="app")
    st.progress(float(state["progress"]), text=t("expense.ears.progress"))


def _render_waiting(svc: ExpenseConfirmService, say, siri_files) -> None:
    """The receipts saved for the next import (spec 32, 4.4bis) and the
    sentences dictated to Siri that were not understood (4.9): how many, and
    in the expander each one with a button to forget it."""
    waiting = svc.pending_receipts()
    unclear = svc.unclear_sentences(siri_files)
    n = len(waiting) + len(unclear)
    if not n:
        return
    title = t("expense.receipts_waiting_one") if n == 1 else t("expense.receipts_waiting", n=n)
    with st.expander(f"🧾 {title}"):
        for r in waiting:
            c_line, c_forget = st.columns([5, 1], vertical_alignment="center")
            line = SimpleNamespace(key="expense.receipt_line", note=None, params={
                "what": r["what"], "day": r["day"], "amount": r["amount"],
                "cat": (r["category"], r["subcategory"]) if r["category"] else t("expense.receipt_no_cat")})
            text = say([line]) + (f" · {t('expense.receipt_cash_tag')}" if r["cash"] else "")
            c_line.write(text)
            if c_forget.button(t("expense.receipt_forget"), key=f"expense_receipt_forget_{r['id']}"):
                svc.delete_receipt(r["id"])
                st.rerun()
        for i, sentence in enumerate(unclear):
            c_line, c_forget = st.columns([5, 1], vertical_alignment="center")
            c_line.write(t("expense.siri_unclear_line", text=sentence))
            if c_forget.button(t("expense.receipt_forget"), key=f"expense_siri_forget_{i}"):
                svc.forget_unclear(siri_files, sentence)
                st.rerun()


def _collect_siri(svc: ExpenseConfirmService, files) -> None:
    """The expenses dictated to Siri since the last time (spec 32, 4.9),
    read once per session, before the conversation starts, so the ones
    with a movement are offered at once."""
    if st.session_state.get("_expense_siri_read"):
        return
    st.session_state["_expense_siri_read"] = True
    try:
        saved, unclear = svc.collect_siri(files)
    except OSError as exc:
        logger.warning(f"expense_confirm_page: the Siri file could not be read: {exc}")
        return
    if saved:
        st.toast(t("expense.siri_saved_one") if saved == 1 else t("expense.siri_saved", n=saved))
    if unclear:
        st.toast(t("expense.siri_unclear", n=unclear))
    # Cash receipts saved before there was a cash account join it now (spec 32, 4.4).
    moved = svc.flush_cash_receipts()
    if moved:
        st.toast(t("expense.cash_flushed", n=moved, account=svc.cash_account()))


def render_expense_confirm_page(engine) -> None:
    st.header(t("expense.title"))
    # Under the title, not beside it: beside it the top bar of the app hid them.
    c_intro, c_speak, c_restart = st.columns([4, 1, 1], vertical_alignment="center")
    c_intro.caption(t("expense.intro"))
    c_speak.toggle(t("expense.speak"), value=True, key="expense_speak", help=t("expense.speak_help"))
    settings = SettingsService(engine).get_all()
    say = _formatter(settings)
    svc = ExpenseConfirmService(engine, rule_writer=_rule_writer(engine))
    siri_files = svc.siri_files(settings.get("siri_icloud_inbox") == "true")
    _collect_siri(svc, siri_files)
    if c_restart.button(t("expense.restart"), key="expense_restart") or _STATE not in st.session_state:
        _reset(svc, say)
    voice_svc = VoiceService()
    if voice_svc.can_offer_listening(get_language()):
        _offer_listening(voice_svc, SettingsService(engine))
    own_voice = st.session_state.get("expense_own_voice", True) and voice_svc.can_listen()
    if own_voice:
        if not st.session_state.get("_expense_warm"):
            st.session_state["_expense_warm"] = True
            voice_svc.warm_up()
        _take_answer(svc, say, voice_svc)
        # Above the chat, always in the same place: below it every new
        # message would move it, the page would make it again and the
        # microphone would be lost.
        _own_turn(voice_svc)

    # A box of fixed height that scrolls by itself to the last message: the
    # recorder above and the input below stay where they are.
    with st.container(height=_CHAT_HEIGHT, key="expense_chat_box"):
        for role, text in st.session_state[_CHAT]:
            with st.chat_message(role):
                st.markdown(text)
    if not own_voice and st.session_state.get("expense_speak", True):
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

    if voice_svc.can_listen():
        st.toggle(t("expense.own_voice"), value=True, key="expense_own_voice", help=t("expense.own_voice_help"))
    _render_waiting(svc, say, siri_files)

    closed = state.step == _CLOSED
    if closed:
        st.caption(t("expense.closed_hint"))
    if text := st.chat_input(t("expense.placeholder"), key="expense_input", disabled=closed):
        _answer(svc, say, text)
        st.rerun()
