"""Chat page — Spendif.ai support chatbot."""
from __future__ import annotations

import re

import streamlit as st

from chat_bot.engine import ChatBotEngine, ChatMode
from services.settings_service import SettingsService
from ui.i18n import t as t_fn

_MODE_LABELS = {
    ChatMode.RAG_CLOUD: "RAG Cloud",
    ChatMode.RAG_LOCAL: "RAG Local",
    ChatMode.FAQ_MATCH: "FAQ",
}

# Pages a chat answer can point to (same keys as sidebar _NAV_KEYS); the
# label is the menu's own, in the user's language.
_PAGE_REFS = frozenset({
    "import", "history", "ledger", "bulk_edit", "analytics", "report", "budget",
    "budget_vs_actual", "review", "rules", "taxonomy", "settings", "checklist",
    "home", "guided", "counterparts", "mixed", "llm_models", "diagnostics",
})
# Pages whose label is not a menu entry (reached from the workflow).
_PAGE_LABEL_KEY = {"mixed": "mixed.title"}
# Sources come best match first; the best page is enough, more is noise.
_MAX_PAGE_LINKS = 1


def page_label(page: str) -> str:
    return t_fn(_PAGE_LABEL_KEY.get(page, f"nav.{page}"))


def _named_page(text: str) -> str | None:
    """The page the answer tells the user to open: the first menu label found
    in the text (the longest one when two start at the same place, so "Budget
    vs Consuntivo" wins over "Budget")."""
    found = []
    for page in _PAGE_REFS:
        name = re.sub(r"^\W+", "", page_label(page)).strip()   # without the icon
        m = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)
        if m:
            found.append((m.start(), -len(name), page))
    return min(found)[2] if found else None


def pages_to_link(sources: list[str], text: str) -> list[str]:
    """The page named in the answer, else the best-matching source page: a link
    must lead where the text says to go."""
    named = _named_page(text)
    if named:
        return [named]
    return [s for s in sources if s in _PAGE_REFS][:_MAX_PAGE_LINKS]


_CHAT_CSS = """
<style>
/* ── WhatsApp-style chat bubbles ─────────────────────────────────── */

/* User messages: right-aligned bubble */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    flex-direction: row-reverse;
    align-items: flex-start;
    background: #dcf8c6;
    border-radius: 16px 4px 16px 16px;
    width: fit-content;
    max-width: 72%;
    height: auto !important;
    min-height: 0 !important;
    margin-left: auto;
    margin-right: 0;
    padding: 6px 10px 4px 10px;
    margin-bottom: 4px;
    box-shadow: 0 1px 1px rgba(0,0,0,.08);
}

/* Assistant messages: left-aligned bubble */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    align-items: flex-start;
    background: #f0f0f0;
    border-radius: 4px 16px 16px 16px;
    width: fit-content;
    max-width: 78%;
    height: auto !important;
    min-height: 0 !important;
    margin-left: 0;
    margin-right: auto;
    padding: 6px 10px 4px 10px;
    margin-bottom: 4px;
    box-shadow: 0 1px 1px rgba(0,0,0,.08);
}

/* Force black text inside all bubbles */
[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li,
[data-testid="stChatMessage"] span,
[data-testid="stChatMessage"] div {
    color: #000000 !important;
}

/* Compact: tighten paragraph spacing inside bubbles */
[data-testid="stChatMessage"] p {
    margin-bottom: 4px !important;
}
[data-testid="stChatMessage"] .stCaptionContainer {
    margin-top: 2px !important;
}
</style>
"""


def render_chat_page(engine) -> None:
    st.markdown(_CHAT_CSS, unsafe_allow_html=True)
    st.header(t_fn("chat.title"))

    settings = SettingsService(engine)
    lang = settings.get_all().get("ui_language", "en")

    # initialise chatbot once per session
    if "chatbot" not in st.session_state:
        st.session_state["chatbot"] = ChatBotEngine(db_engine=engine, lang=lang)
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    bot: ChatBotEngine = st.session_state["chatbot"]

    # mode badge
    mode_label = _MODE_LABELS.get(bot.mode, bot.mode.value)
    st.caption(t_fn("chat.mode_label").format(mode=mode_label))

    # suggested questions (only when history is empty). The container is there
    # in every run so the messages below keep their place: otherwise, after the
    # first answer, the reply being computed shows the previous one faded.
    with st.container():
        if not st.session_state["chat_history"]:
            st.markdown(f"*{t_fn('chat.welcome')}*")
            _render_suggestions(bot, lang)

    # render conversation history
    for n, msg in enumerate(st.session_state["chat_history"]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                _render_sources(msg["sources"], msg["content"], key=f"h{n}")

    # chat input
    if user_input := st.chat_input(t_fn("chat.placeholder")):
        # show user message
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # get bot response (pass history excluding the just-added user message)
        history_so_far = st.session_state["chat_history"][:-1]
        # A fresh slot: whatever an earlier run left in this place is cleared
        # before the spinner shows.
        with st.empty().container():
            with st.chat_message("assistant"):
                with st.spinner(t_fn("chat.thinking")):
                    response = bot.ask(user_input, history=history_so_far or None)
                st.markdown(response.text)
                if response.sources:
                    _render_sources(response.sources, response.text, key="new")

        st.session_state["chat_history"].append({
            "role": "assistant",
            "content": response.text,
            "sources": response.sources,
        })

    # clear chat button
    if st.session_state["chat_history"]:
        if st.button(t_fn("chat.clear"), type="secondary"):
            st.session_state["chat_history"] = []
            st.rerun()


def _render_suggestions(bot: ChatBotEngine, lang: str) -> None:
    """Show clickable suggested questions."""
    suggestions = _get_suggestions(lang)
    if not suggestions:
        return
    cols = st.columns(len(suggestions))
    for col, suggestion in zip(cols, suggestions):
        with col:
            if st.button(suggestion, key=f"suggest_{suggestion[:20]}", use_container_width=True):
                st.session_state["chat_history"].append({"role": "user", "content": suggestion})
                response = bot.ask(suggestion)
                st.session_state["chat_history"].append({
                    "role": "assistant",
                    "content": response.text,
                    "sources": response.sources,
                })
                st.rerun()


def _get_suggestions(lang: str) -> list[str]:
    """Domande suggerite, dal catalogo i18n.

    Prima vivevano in un dizionario locale: tradotte, ma in una seconda fonte
    di verita' che nessuno teneva allineata con i file di traduzione.
    """
    from ui.i18n import set_language, t

    set_language(lang)
    return [t(f"chat.suggestion.{k}") for k in ("import", "formats", "category")]


def _render_sources(sources: list[str], text: str, key: str) -> None:
    """The page the answer came from, as a button that opens it; the guide's
    name only when no page is known."""
    pages = pages_to_link(sources, text)
    docs = [] if pages else [
        _format_source(s) for s in sources if s not in _PAGE_REFS and _format_source(s)]
    if pages:
        cols = st.columns(len(pages))
        for col, page in zip(cols, pages):
            with col:
                if st.button(f"→ {page_label(page)}", key=f"src_{key}_{page}", type="tertiary"):
                    from ui.widgets.immersive import go_to
                    go_to(page)
    if docs:
        st.caption("  ·  ".join(docs))


def _format_source(src: str) -> str:
    """Format a source reference for display.

    - Known page_ref keys  → "→ 📥 Import" (navigable page label)
    - Doc filenames (.md)  → "📄 guida_utente.md"
    - Anything else        → suppressed (return empty string)
    """
    if not src:
        return ""
    if src in _PAGE_REFS:
        return f"→ {page_label(src)}"
    if src.endswith((".md", ".txt", ".pdf")):
        return f"📄 {src}"
    return ""
