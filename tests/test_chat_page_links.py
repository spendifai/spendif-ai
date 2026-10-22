"""The link under a bot answer leads to the page the answer names."""
from __future__ import annotations

import pytest

from ui.chat_page import pages_to_link
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _keep_language():
    before = get_language()
    yield
    set_language(before)


@pytest.mark.parametrize("text, page", [
    ("Apri Prepariamo i tuoi movimenti, la prima voce del menu; poi le Controparti", "guided"),
    ("Vai su **Analisi**. Trovi i grafici.", "analytics"),
    ("Apri Budget vs Consuntivo per i semafori", "budget_vs_actual"),
])
def test_the_named_page_wins_over_the_sources(text, page):
    set_language("it")
    assert pages_to_link(["settings", "report"], text) == [page]


def test_without_a_named_page_the_best_source_is_linked():
    set_language("it")
    assert pages_to_link(["settings", "report"], "Nessuna pagina citata") == ["settings"]


def test_labels_follow_the_language():
    set_language("en")
    assert pages_to_link(["settings"], "Go to **Analytics**") == ["analytics"]
