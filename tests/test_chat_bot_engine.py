"""Which mode the support bot picks, and which backend it builds, from the settings."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chat_bot.engine import ChatBotEngine, ChatMode


def _engine_with(settings: dict) -> ChatBotEngine:
    with patch.object(ChatBotEngine, "_read_settings", return_value=settings):
        eng = ChatBotEngine(db_engine=object(), lang="it")
    eng._read_settings = lambda: settings
    return eng


@pytest.mark.parametrize("backend", ["local_llama_cpp", "local_ollama", "vllm"])
def test_local_backends_use_the_model(backend):
    assert _engine_with({"llm_backend": backend}).mode == ChatMode.RAG_LOCAL


def test_nothing_configured_is_faq_only():
    assert _engine_with({}).mode == ChatMode.FAQ_MATCH
    assert ChatBotEngine(db_engine=None, lang="it").mode == ChatMode.FAQ_MATCH


@pytest.mark.parametrize("backend,key", [
    ("openai", "openai_api_key"),
    ("claude", "anthropic_api_key"),
    ("openai_compatible", "compat_api_key"),
])
def test_cloud_needs_its_own_key(backend, key):
    assert _engine_with({"llm_backend": backend, key: "k"}).mode == ChatMode.RAG_CLOUD
    assert _engine_with({"llm_backend": backend}).mode == ChatMode.FAQ_MATCH


def test_llama_cpp_built_with_the_import_kwargs():
    eng = _engine_with({
        "llm_backend": "local_llama_cpp",
        "llama_cpp_model_path": "/m/gemma.gguf",
        "llama_cpp_n_gpu_layers": "20",
        "llama_cpp_n_ctx": "8192",
    })
    factory = MagicMock()
    eng._build_local_backend(factory)
    factory.create.assert_called_once_with(
        "local_llama_cpp", model_path="/m/gemma.gguf", n_gpu_layers=20, n_ctx=8192)


def test_ollama_uses_the_configured_model():
    eng = _engine_with({"llm_backend": "local_ollama", "ollama_model": "qwen3:8b",
                        "ollama_base_url": "http://box:11434"})
    factory = MagicMock()
    eng._build_local_backend(factory)
    factory.create.assert_called_once_with("local_ollama", base_url="http://box:11434",
                                           model="qwen3:8b")


def test_vllm_is_not_routed_to_ollama():
    eng = _engine_with({"llm_backend": "vllm", "vllm_base_url": "http://g:8000",
                        "vllm_model": "m"})
    factory = MagicMock()
    eng._build_local_backend(factory)
    assert factory.create.call_args[0][0] == "vllm"


def test_claude_uses_the_model_chosen_in_settings():
    eng = _engine_with({"llm_backend": "claude", "anthropic_api_key": "k",
                        "anthropic_model": "claude-haiku-4-5-20251001"})
    with patch("core.llm_backends.BackendFactory.create") as create:
        eng._build_cloud_backend()
    assert create.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_busy_model_answers_from_faq_and_says_why():
    eng = _engine_with({"llm_backend": "local_llama_cpp"})
    eng._rag = MagicMock()
    eng._backend = MagicMock()
    eng._backend.is_busy.return_value = True
    resp = eng.ask("Come importo un file?")
    eng._rag.query.assert_not_called()
    assert resp.mode == ChatMode.FAQ_MATCH
    assert resp.text.startswith(eng._busy_note())


# ── Nothing loaded yet after the onboarding ──────────────────────────────────

@pytest.fixture
def empty_archive():
    from sqlalchemy import create_engine, text
    from db.models import create_tables
    db = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(db)
    with db.begin() as conn:
        conn.execute(text("INSERT OR REPLACE INTO user_settings (key, value) VALUES ('onboarding_done', 'true')"))
        conn.execute(text("INSERT OR REPLACE INTO user_settings (key, value) VALUES ('llm_backend', '')"))
    return db


def _add_movement(db):
    from decimal import Decimal
    from db.models import Transaction, get_session
    with get_session(db) as s:
        s.add(Transaction(id="t1", date="2026-10-01", amount=Decimal("-5"), currency="EUR",
                          description="Bar", source_file="f.csv", doc_type="bank_account",
                          account_label="A", tx_type="expense", reconciled=False, to_review=False))
        s.commit()


def test_without_a_model_the_answer_opens_with_the_advice_to_load(empty_archive):
    eng = ChatBotEngine(db_engine=empty_archive, lang="it")
    assert eng.mode == ChatMode.FAQ_MATCH
    resp = eng.ask("Come importo un file?")
    assert resp.text.startswith(eng._note("state_no_data_user"))


def test_a_question_without_answer_gets_the_advice_alone(empty_archive):
    eng = ChatBotEngine(db_engine=empty_archive, lang="it")
    eng._classifier = None
    assert eng.ask("Quanto ho speso al supermercato?").text == eng._note("state_no_data_user")


def test_the_model_is_told_that_nothing_is_loaded(empty_archive):
    eng = ChatBotEngine(db_engine=empty_archive, lang="it")
    eng._mode = ChatMode.RAG_LOCAL
    eng._rag = MagicMock()
    eng._rag.query.return_value = MagicMock(answer="Prima carica un estratto.", sources=[])
    eng.ask("Quanto ho speso al supermercato?")
    assert eng._rag.query.call_args.kwargs["state_note"] == eng._note("state_no_data_model")


def test_the_first_movement_ends_it(empty_archive):
    _add_movement(empty_archive)
    eng = ChatBotEngine(db_engine=empty_archive, lang="it")
    eng._mode = ChatMode.RAG_LOCAL
    eng._rag = MagicMock()
    eng._rag.query.return_value = MagicMock(answer="ok", sources=[])
    eng.ask("Quanto ho speso al supermercato?")
    assert eng._rag.query.call_args.kwargs["state_note"] == ""
    eng._mode = ChatMode.FAQ_MATCH
    assert not eng.ask("Come importo un file?").text.startswith(eng._note("state_no_data_user"))


@pytest.mark.parametrize("key", ["state_no_data_model", "state_no_data_user"])
def test_the_advice_is_in_every_language(key):
    import json
    from chat_bot.engine import _PROMPTS_PATH
    assert set(json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))[key]) == {"it", "en", "fr", "de", "es"}
