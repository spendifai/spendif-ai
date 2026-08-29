"""Guard sulle chiavi di traduzione.

Il modo in cui una etichetta tradotta si rompe non e' una eccezione: t() su
chiave mancante restituisce la chiave stessa, quindi l'utente vede
"review.tx_meta" al posto del testo e nessun test fallisce. Questi controlli
rendono visibile quel caso.
"""
import json
import re
from pathlib import Path

import pytest

I18N_DIR = Path(__file__).resolve().parents[1] / "ui" / "i18n"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(lang: str) -> dict:
    # encoding esplicito: su Windows read_text() userebbe cp1252 e i file
    # di traduzione sono UTF-8
    return json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


LINGUE = sorted(p.stem for p in I18N_DIR.glob("*.json"))


def test_all_shipped_languages_have_the_same_keys():
    """Tutte le lingue spedite, non solo it/en. Il primo giro di questo test
    confrontava solo le due principali, e le chiavi aggiunte quel giorno sono
    rimaste fuori da fr, de ed es senza far fallire nulla: l'utente francese
    leggeva italiano per via del fallback."""
    riferimento = _load("it")
    buchi = {}
    for lang in LINGUE:
        mancanti = sorted(set(riferimento) - set(_load(lang)))
        se_ne_piu = sorted(set(_load(lang)) - set(riferimento))
        if mancanti or se_ne_piu:
            buchi[lang] = {"mancanti": mancanti[:8], "in_piu": se_ne_piu[:8]}
    assert not buchi, f"cataloghi disallineati: {buchi}"


@pytest.mark.parametrize("lang", [l for l in sorted(p.stem for p in I18N_DIR.glob("*.json")) if l != "it"])
def test_placeholders_match_the_reference(lang):
    """Un {placeholder} presente in una lingua e non nell'altra fa esplodere
    .format() a runtime, e solo per chi usa quella lingua."""
    it, altra = _load("it"), _load(lang)
    disallineate = {
        k: (sorted(PLACEHOLDER.findall(it[k])), sorted(PLACEHOLDER.findall(altra[k])))
        for k in it.keys() & altra.keys()
        if sorted(PLACEHOLDER.findall(it[k])) != sorted(PLACEHOLDER.findall(altra[k]))
    }
    assert not disallineate, f"{lang}: placeholder disallineati: {disallineate}"


@pytest.mark.parametrize("lang", ["it", "en"])
def test_review_tx_meta_interpolates(lang):
    """La riga di riepilogo sopra il form di correzione: se la chiave sparisce
    o cambia placeholder, l'utente si ritrova la chiave stampata a schermo."""
    from ui.i18n import set_language, t

    set_language(lang)
    out = t("review.tx_meta", type="expense", cat="Casa", sub="Gas", conf="0.91")
    assert out != "review.tx_meta", "chiave mancante: t() ha restituito la chiave"
    assert "expense" in out and "Casa" in out and "Gas" in out and "0.91" in out
    assert "{" not in out, f"placeholder non sostituito: {out}"


def test_ledger_row_keys_cover_the_column_config():
    """Le colonne configurate devono esistere nelle righe. Prima le due cose
    erano etichette tradotte: in inglese non combaciavano piu' e Streamlit
    ignorava in silenzio la configurazione delle colonne che non trovava."""
    from ui import registry_page as rp

    class _Tx:
        id, date, description, raw_description = "abc", "2026-08-28", "COOP", "COOP RAW"
        amount, account_label, tx_type = -12.5, "Conto", "expense"
        category, subcategory, context = "Alimentari", "Spesa supermercato", ""
        category_source, to_review, human_validated = "llm", False, True

    for show_raw in (False, True):
        row = rp.build_ledger_row(_Tx(), show_raw, {"llm": "🤖"})
        attese = {
            rp.COL_ID, rp.COL_SEL, rp.COL_DATE, rp.COL_DESC, rp.COL_INCOME, rp.COL_EXPENSE,
            rp.COL_ACCOUNT, rp.COL_TYPE, rp.COL_CATEGORY, rp.COL_SUBCATEGORY, rp.COL_CONTEXT,
            rp.COL_SOURCE, rp.COL_FLAG_REVIEW, rp.COL_FLAG_VALID, rp.COL_FLAG_TRANSFER,
            rp.COL_VALIDATED, rp.COL_TRANSFER,
        }
        if show_raw:
            attese.add(rp.COL_RAW)
        assert set(row) == attese, f"show_raw={show_raw}: {set(row) ^ attese}"


def test_ledger_row_keys_do_not_depend_on_language():
    """Il bug originale: le chiavi cambiavano con la lingua, e il salvataggio
    delle modifiche indicizzava colonne inesistenti (KeyError)."""
    from ui import registry_page as rp
    from ui.i18n import set_language

    class _Tx:
        id, date, description, raw_description = "abc", "2026-08-28", "COOP", ""
        amount, account_label, tx_type = 10.0, "Conto", "income"
        category, subcategory, context = "", "", ""
        category_source, to_review, human_validated = None, True, False

    set_language("it")
    chiavi_it = set(rp.build_ledger_row(_Tx(), False, {}))
    set_language("en")
    chiavi_en = set(rp.build_ledger_row(_Tx(), False, {}))
    assert chiavi_it == chiavi_en


def test_no_duplicate_keys():
    """json.loads tiene l'ultima occorrenza e non protesta: una chiave scritta
    due volte sovrascrive in silenzio la prima, e la stringa che si vede a
    schermo non e' quella che si e' appena modificata."""
    import re
    from collections import Counter

    for lang in sorted(p.stem for p in I18N_DIR.glob("*.json")):
        righe = (I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")
        chiavi = re.findall(r'^\s*"([^"]+)":', righe, re.M)
        dup = [k for k, n in Counter(chiavi).items() if n > 1]
        assert not dup, f"{lang}.json: chiavi duplicate {dup}"
