"""Il testo del movimento come lo scrive la banca si chiama "descrizione
originale", in ogni lingua.

Nelle stesse schermate convivono la descrizione ripulita dall'app e quella del
file: chiamarle tutte e due "descrizione" le rende indistinguibili, e "raw" e'
una parola tecnica che l'utente non incontra altrove (lessico utente, §5.1,
rivisto il 2026-10-25).
"""
import json
import re
from pathlib import Path

import pytest

I18N_DIR = Path(__file__).resolve().parents[1] / "ui" / "i18n"
LINGUE = sorted(p.stem for p in I18N_DIR.glob("*.json"))
# "raw" come parola; i segnaposto come {same_raw} si tolgono prima
RAW = re.compile(r"(?i)(?<![a-z])raw(?![a-z])")
PLACEHOLDER = re.compile(r"\{\w+\}")


def _load(lang: str) -> dict:
    return json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", LINGUE)
def test_no_visible_string_says_raw(lang):
    trovate = {k: v for k, v in _load(lang).items()
               if isinstance(v, str) and RAW.search(PLACEHOLDER.sub("", v))}
    assert not trovate, f"{lang}: {trovate}"


@pytest.mark.parametrize("lang, atteso", [
    ("it", "Mostra descrizione originale"),
    ("en", "Show original description"),
    ("fr", "Afficher le libellé d'origine"),
    ("de", "Ursprünglichen Verwendungszweck anzeigen"),
    ("es", "Mostrar concepto original"),
])
def test_show_original_description_toggle(lang, atteso):
    assert _load(lang)["ledger.filter.show_raw"] == atteso
