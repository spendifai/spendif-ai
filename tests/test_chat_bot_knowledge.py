"""The bot's knowledge: copied from the guides, cut at headings, one language at a time."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from chat_bot.engine import _context_budget, _knowledge_for
from chat_bot.kb_store import DocChunk, FAQEntry, _split_sections
from chat_bot.rag import RAGEngine

ROOT = Path(__file__).resolve().parent.parent


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "build_chat_knowledge", ROOT / "tools" / "build_chat_knowledge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_knowledge_matches_the_guides():
    """A guide changed without regenerating the bot's copy: the bot would answer
    from the old text. Fix with: python tools/build_chat_knowledge.py"""
    assert _load_tool().stale() == []


def test_every_source_guide_exists():
    for src, _ in _load_tool().plan():
        assert src.exists(), src


def test_chunks_carry_their_heading_path():
    text = "# Guide\n\nintro\n\n## 5. Rules\n\nwhy\n\n### Create a rule\n\nstep 1\n\nstep 2\n"
    chunks = _split_sections(text, 1500)
    assert "Guide > 5. Rules > Create a rule\n\nstep 1\n\nstep 2" in chunks


def test_long_section_repeats_the_heading_in_every_piece():
    body = "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(6))
    chunks = _split_sections(f"## Import\n\n{body}\n", 800)
    assert len(chunks) > 1
    assert all(c.startswith("Import\n\n") for c in chunks)


def test_hash_inside_code_is_not_a_heading():
    chunks = _split_sections("## Setup\n\n```\n# comment\n```\n", 1500)
    assert chunks == ["Setup\n\n```\n# comment\n```"]


def _faq(lang):
    return FAQEntry(question="q", answer="a", lang=lang)


def _doc(lang):
    return DocChunk(text="t", lang=lang)


def test_only_the_users_language_is_kept():
    faq, docs = _knowledge_for("it", [_faq("it"), _faq("en"), _faq("common")],
                               [_doc("it"), _doc("en")])
    assert {e.lang for e in faq} == {"it", "common"}
    assert {c.lang for c in docs} == {"it"}


def test_language_without_knowledge_falls_back_to_english():
    faq, docs = _knowledge_for("fr", [_faq("it"), _faq("en")], [_doc("it"), _doc("en")])
    assert {e.lang for e in faq} == {"en"}
    assert {c.lang for c in docs} == {"en"}


class _Backend:
    def __init__(self, n_ctx):
        self._n_ctx = n_ctx

    def get_context_info(self):
        return {"n_ctx": self._n_ctx} if self._n_ctx else None


def test_context_budget_follows_the_window():
    assert _context_budget(_Backend(None)) is None
    assert _context_budget(_Backend(4096)) == 5734
    assert _context_budget(_Backend(2048)) == 3000      # floor
    assert _context_budget(_Backend(131072)) == 12000   # cap


def _rag(chunks, budget, top_k=5):
    return RAGEngine(faq_entries=[], doc_chunks=[DocChunk(text=t, lang="it") for t in chunks],
                     llm_backend=None, top_k=top_k, lang="it", context_chars=budget)


def test_selection_respects_the_budget():
    rag = _rag(["a" * 400, "b" * 400, "c" * 400], budget=900)
    picked = rag._select_within_budget([(0.9, 0), (0.8, 1), (0.7, 2)])
    assert sum(len(e.text) for e in picked) <= 900
    assert len(picked) == 2


def test_selection_never_returns_nothing_when_something_matches():
    rag = _rag(["a" * 5000], budget=1000)
    assert len(rag._select_within_budget([(0.9, 0)])) == 1


def test_entries_without_overlap_are_not_passed():
    rag = _rag(["a", "b"], budget=1000)
    assert len(rag._select_within_budget([(0.9, 0), (0.0, 1)])) == 1


def test_stopwords_do_not_count():
    tokens = RAGEngine._tokenize("Come faccio a creare una regola per il supermercato?")
    assert "come" not in tokens and "faccio" not in tokens and "il" not in tokens
    assert {"creare", "regola", "supermercato"} <= set(tokens)
