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
    return json.loads((I18N_DIR / f"{lang}.json").read_text())


def test_it_and_en_have_the_same_keys():
    it, en = _load("it"), _load("en")
    solo_it = sorted(set(it) - set(en))
    solo_en = sorted(set(en) - set(it))
    assert not solo_it, f"chiavi presenti solo in it.json: {solo_it[:10]}"
    assert not solo_en, f"chiavi presenti solo in en.json: {solo_en[:10]}"


def test_placeholders_match_across_languages():
    """Un {placeholder} presente in una lingua e non nell'altra fa esplodere
    .format() a runtime, sulla lingua sbagliata."""
    it, en = _load("it"), _load("en")
    disallineate = {
        k: (sorted(PLACEHOLDER.findall(it[k])), sorted(PLACEHOLDER.findall(en[k])))
        for k in it.keys() & en.keys()
        if sorted(PLACEHOLDER.findall(it[k])) != sorted(PLACEHOLDER.findall(en[k]))
    }
    assert not disallineate, f"placeholder disallineati: {disallineate}"


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
