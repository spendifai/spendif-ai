"""
ChatBotEngine — adaptive chatbot that auto-selects the best mode.

Priority order:
1. RAG Cloud  — if external API is configured (Claude/OpenAI)
2. RAG Local: local model: llama.cpp in-process, Ollama or vLLM
3. FAQ Match  — deterministic fallback, no LLM needed
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from chat_bot.faq_classifier import ClassifierResult, FAQClassifier
from chat_bot.kb_store import load_documents, load_faq
from chat_bot.rag import RAGEngine, RAGResult

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent / "prompts.json"

# Setting that holds the API key of each cloud backend.
_API_KEY_SETTING = {
    "openai": "openai_api_key",
    "claude": "anthropic_api_key",
    "openai_compatible": "compat_api_key",
}


class ChatMode(str, Enum):
    RAG_CLOUD = "rag_cloud"
    RAG_LOCAL = "rag_local"
    FAQ_MATCH = "faq_match"


@dataclass
class ChatResponse:
    text: str
    mode: ChatMode
    confidence: float | None = None
    sources: list[str] | None = None


class ChatBotEngine:
    """
    Adaptive chatbot engine.

    Usage:
        engine = ChatBotEngine(db_engine, lang="it")
        response = engine.ask("Come importo un file?")
    """

    def __init__(self, db_engine=None, lang: str = "it"):
        self._db_engine = db_engine
        self._lang = lang
        self._prompts = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))

        # knowledge in the person's language only: mixing languages in one index
        # let a short English FAQ outrank the Italian section that answers
        self._faq_entries, self._doc_chunks = _knowledge_for(lang, *_load_all_knowledge())

        # always build FAQ classifier (zero cost)
        self._classifier = FAQClassifier(self._faq_entries) if self._faq_entries else None

        # detect available mode
        self._mode = self._detect_mode()
        self._rag: RAGEngine | None = None
        self._backend = None

        logger.info("ChatBot initialized — mode=%s, faq=%d, docs=%d",
                     self._mode.value, len(self._faq_entries), len(self._doc_chunks))

    @property
    def mode(self) -> ChatMode:
        return self._mode

    def ask(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse:
        """Answer a user question using the best available mode.

        Args:
            question: The current user question.
            history: Optional list of previous turns as
                     ``[{"role": "user"|"assistant", "content": "..."}]``.
                     Passed to RAG so the model can resolve follow-up questions.
        """
        question = question.strip()
        if not question:
            return ChatResponse(text=self._no_answer(), mode=self._mode)

        # Nothing loaded yet after the onboarding: the model is told, so it
        # sends the person to load a statement first and invents no figures;
        # without a model the answer opens with the same advice.
        no_data = self._no_data_yet()
        if self._mode in (ChatMode.RAG_CLOUD, ChatMode.RAG_LOCAL):
            resp = self._ask_rag(question, history=history,
                                 state_note=self._note("state_no_data_model") if no_data else "")
        else:
            resp = self._ask_faq(question)
        if no_data:
            user_note = self._note("state_no_data_user")
            if resp.text == self._no_answer():
                resp.text = user_note
            elif resp.mode == ChatMode.FAQ_MATCH:
                resp.text = f"{user_note}\n\n{resp.text}"
        return resp

    # ── mode detection ───────────────────────────────────────────────

    def _detect_mode(self) -> ChatMode:
        """Detect chatbot mode from the user's LLM settings.

        The mode follows whatever the user configured in Settings:
        - Cloud API (openai, claude, openai_compatible) → RAG Cloud
        - Local backend (local_llama_cpp, local_ollama, vllm) → RAG Local
        - None configured → FAQ Match
        """
        if self._db_engine is None:
            return ChatMode.FAQ_MATCH
        try:
            settings = self._read_settings()
            backend = settings.get("llm_backend", "")

            # cloud backends: need a valid API key
            if backend in ("openai", "claude", "openai_compatible"):
                key = settings.get(_API_KEY_SETTING[backend])
                if key:
                    return ChatMode.RAG_CLOUD

            # local backends: the in-process model or a local server
            if backend in ("local_llama_cpp", "local_ollama", "vllm"):
                return ChatMode.RAG_LOCAL

        except Exception:
            pass
        # unconfigured → deterministic FAQ
        return ChatMode.FAQ_MATCH

    def _no_data_yet(self) -> bool:
        """True when the person finished the onboarding and has done nothing
        else: no movement and no import in the archive. Counted in the DB,
        at every question, so the first import ends it at once."""
        if self._db_engine is None:
            return False
        try:
            from sqlalchemy.orm import Session
            from db.models import ImportBatch, Transaction
            if self._read_settings().get("onboarding_done", "false").lower() != "true":
                return False
            with Session(self._db_engine) as session:
                return (session.query(Transaction.id).limit(1).first() is None
                        and session.query(ImportBatch.id).limit(1).first() is None)
        except Exception:
            return False

    def _read_settings(self) -> dict[str, str]:
        """Read user settings from DB."""
        from db.repository import get_all_user_settings
        from sqlalchemy.orm import Session
        with Session(self._db_engine) as session:
            return get_all_user_settings(session)

    # ── ask implementations ──────────────────────────────────────────

    def _ask_rag(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
        state_note: str = "",
    ) -> ChatResponse:
        """Answer using RAG (cloud or local)."""
        if self._rag is None:
            self._rag = self._build_rag()
        if self._rag is None:
            # fallback to FAQ if backend construction fails
            return self._ask_faq(question)
        if self._backend is not None and self._backend.is_busy():
            # The in-process model is serving an import: answer now from the
            # FAQ rather than wait behind it, and say why.
            resp = self._ask_faq(question)
            resp.text = f"{self._busy_note()}\n\n{resp.text}"
            return resp
        result: RAGResult = self._rag.query(question, history=history, state_note=state_note)
        return ChatResponse(
            text=result.answer,
            mode=self._mode,
            sources=result.sources,
        )

    def _ask_faq(self, question: str) -> ChatResponse:
        """Answer using deterministic FAQ matching."""
        if self._classifier is None:
            return ChatResponse(text=self._no_answer(), mode=ChatMode.FAQ_MATCH)
        result: ClassifierResult | None = self._classifier.classify(question)
        if result is None:
            return ChatResponse(text=self._no_answer(), mode=ChatMode.FAQ_MATCH, confidence=0.0)
        return ChatResponse(
            text=result.answer,
            mode=ChatMode.FAQ_MATCH,
            confidence=result.confidence,
            sources=[result.source] if result.source else None,
        )

    # ── backend construction ─────────────────────────────────────────

    def _build_rag(self) -> RAGEngine | None:
        """Build RAG engine with the appropriate LLM backend."""
        try:
            from core.llm_backends import BackendFactory
            if self._mode == ChatMode.RAG_CLOUD:
                backend = self._build_cloud_backend()
            else:
                backend = self._build_local_backend(BackendFactory)
            if backend is None:
                return None
            self._backend = backend
            return RAGEngine(
                faq_entries=self._faq_entries,
                doc_chunks=self._doc_chunks,
                llm_backend=backend,
                lang=self._lang,
                context_chars=_context_budget(backend),
            )
        except Exception as e:
            logger.warning("Failed to build RAG engine: %s", e)
            return None

    def _build_cloud_backend(self):
        """Build a cloud LLM backend from user settings."""
        from core.llm_backends import BackendFactory
        settings = self._read_settings()
        backend_name = settings.get("llm_backend", "openai")
        kwargs = {}
        # Same setting keys the import reads, so the chat uses the model the
        # person chose in Settings.
        if backend_name == "openai":
            kwargs["api_key"] = settings.get("openai_api_key", "")
            kwargs["model"] = settings.get("openai_model", "gpt-4o-mini")
        elif backend_name == "claude":
            kwargs["api_key"] = settings.get("anthropic_api_key", "")
            kwargs["model"] = settings.get("anthropic_model", "claude-haiku-4-5-20251001")
        elif backend_name == "openai_compatible":
            kwargs["api_key"] = settings.get("compat_api_key", "")
            kwargs["base_url"] = settings.get("compat_base_url", "")
            kwargs["model"] = settings.get("compat_model", "")
        return BackendFactory.create(backend_name, **kwargs)

    def _build_local_backend(self, factory):
        """Build the local backend the person configured, with the import's kwargs.

        For llama.cpp the kwargs match the import's exactly, so both resolve to
        the same entry of the shared model pool: one copy of the weights.
        """
        settings = self._read_settings()
        backend_name = settings.get("llm_backend", "")
        if backend_name == "local_llama_cpp":
            kwargs = {
                "n_gpu_layers": int(settings.get("llama_cpp_n_gpu_layers", "-1")),
                "n_ctx": int(settings.get("llama_cpp_n_ctx", "0")),
            }
            if settings.get("llama_cpp_model_path"):
                kwargs["model_path"] = settings["llama_cpp_model_path"]
            return factory.create("local_llama_cpp", **kwargs)
        if backend_name == "local_ollama":
            return factory.create(
                "local_ollama",
                base_url=settings.get("ollama_base_url", "http://localhost:11434"),
                model=settings.get("ollama_model", "gemma3:12b"),
            )
        if backend_name == "vllm":
            return factory.create(
                "vllm",
                base_url=settings.get("vllm_base_url", ""),
                model=settings.get("vllm_model", ""),
                api_key=settings.get("vllm_api_key", "") or "none",
            )
        return None

    # ── helpers ──────────────────────────────────────────────────────

    def _note(self, key: str) -> str:
        note = self._prompts.get(key, {})
        return note.get(self._lang, note.get("en", ""))

    def _busy_note(self) -> str:
        note = self._prompts.get("model_busy", {})
        return note.get(self._lang, note.get("en", ""))

    def _no_answer(self) -> str:
        no_answer = self._prompts.get("no_answer", {})
        return no_answer.get(self._lang, no_answer.get("en", "No answer available."))


def _knowledge_for(lang: str, faq_entries: list, doc_chunks: list) -> tuple[list, list]:
    """Keep the entries in ``lang`` plus the language-neutral ones.

    A language without its own knowledge (fr, de, es...) falls back to English.
    """
    langs = {e.lang for e in faq_entries} | {c.lang for c in doc_chunks}
    use = lang if lang in langs else "en"
    keep = {use, "common", ""}
    return ([e for e in faq_entries if e.lang in keep],
            [c for c in doc_chunks if c.lang in keep])


def _context_budget(backend) -> int | None:
    """Characters of retrieved text that fit the model's window.

    About 3.5 characters per token, 40% of the window for the retrieved text
    (the rest is instructions, question, history and answer), capped so a
    large window does not make every answer slow on a local model.
    """
    try:
        info = backend.get_context_info() or {}
    except Exception:
        return None
    n_ctx = info.get("n_ctx")
    if not n_ctx:
        return None
    return max(3000, min(int(n_ctx * 3.5 * 0.4), 12000))


def _load_all_knowledge() -> tuple[list, list]:
    """Load FAQ entries and doc chunks from all available language directories."""
    from pathlib import Path
    knowledge_dir = Path(__file__).parent / "knowledge"
    faq_entries: list = []
    doc_chunks: list = []
    for lang_dir in sorted(knowledge_dir.iterdir()):
        if lang_dir.is_dir():
            faq_entries.extend(load_faq(lang_dir.name))
            doc_chunks.extend(load_documents(lang_dir.name))
    return faq_entries, doc_chunks
