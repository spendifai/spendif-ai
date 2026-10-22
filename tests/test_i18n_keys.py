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
            rp.COL_CAT_SUB, rp.COL_CONTEXT,
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


def test_category_and_subcategory_are_one_column_in_every_grid():
    """Founder, 2026-10-21: in every grid category and subcategory are one
    column ("Category / Subcategory"), as in the counterparts step. No page
    shows the subcategory as a column of its own."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "ui"
    for page in root.glob("*_page.py"):
        source = page.read_text(encoding="utf-8")
        for separate in ('t("ledger.col.subcategory"):', 't("rules.col_subcategory"):',
                         't("analytics.assoc.col.subcategory")', '"Sottocategoria": row'):
            assert separate not in source, f"{page.name}: {separate}"
    from ui import registry_page as rp
    assert not hasattr(rp, "COL_SUBCATEGORY")


def test_one_menu_for_category_and_subcategory_in_the_forms():
    """The forms (Review, Bulk edit, Rules) use one menu of valid pairs too;
    a direction keeps only its categories."""
    from streamlit.testing.v1 import AppTest

    def _form():
        from core.categorizer import TaxonomyConfig
        from ui.widgets.cat_select import cat_sub_select
        import streamlit as st
        tax = TaxonomyConfig(expenses={"Casa": ["Affitto", "Mutuo"], "Cibo": ["Spesa"]},
                             income={"Lavoro": ["Stipendio"]})
        st.session_state["picked"] = cat_sub_select("x", tax, "Casa", "Mutuo", key="k")
        st.session_state["picked_gone"] = cat_sub_select("y", tax, "Casa", "Sparita", key="k2")
        st.session_state["only_income"] = cat_sub_select("z", tax, None, None, key="k3", only="income")

    at = AppTest.from_function(_form).run()
    assert not at.exception
    assert at.session_state["picked"] == ("Casa", "Mutuo")
    assert at.session_state["picked_gone"] == ("Casa", "Affitto")
    assert at.session_state["only_income"] == ("Lavoro", "Stipendio")
    assert at.selectbox[2].options == ["Lavoro / Stipendio"]


def test_every_literal_call_passes_the_placeholders_of_its_text():
    """t("key", a=...) must give every {placeholder} of the Italian text: a
    missing one is a KeyError on screen (2026-10-21, a key reused with a
    different text in the Review page)."""
    import json
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    texts = json.loads((root / "ui" / "i18n" / "it.json").read_text(encoding="utf-8"))
    call = re.compile(r'\bt\(\s*"([a-z0-9_.]+)"\s*((?:,\s*\w+\s*=[^,()]*(?:\([^()]*\))?)*)\s*\)')
    problems = []
    for path in (root / "ui").rglob("*.py"):
        for key, args in call.findall(path.read_text(encoding="utf-8")):
            if key not in texts or not args.strip():
                continue  # no keywords: the caller formats the text itself
            given = set(re.findall(r"(\w+)\s*=", args))
            needed = set(re.findall(r"\{(\w+)\}", texts[key]))
            if needed - given:
                problems.append(f"{path.name}: {key} lacks {sorted(needed - given)}")
    assert not problems, problems


def test_categories_follow_the_direction_of_the_movement():
    from core.categorizer import TaxonomyConfig
    from ui.widgets.cat_select import options_for_amounts, wrong_direction
    tax = TaxonomyConfig(expenses={"Casa": ["Affitto"]}, income={"Lavoro": ["Stipendio"]})
    assert options_for_amounts(tax, [-5, -7]) == ["Casa / Affitto"]
    assert options_for_amounts(tax, [100]) == ["Lavoro / Stipendio"]
    assert options_for_amounts(tax, [-5, 100]) == ["Casa / Affitto", "Lavoro / Stipendio"]
    assert wrong_direction(tax, "Lavoro", -5) and wrong_direction(tax, "Casa", 100)
    assert not wrong_direction(tax, "Casa", -5) and not wrong_direction(tax, "", -5)


def test_the_bank_text_sits_next_to_the_description():
    from ui import registry_page as rp

    class _Tx:
        id, date, description, raw_description = "abc", "2026-08-28", "Coop", "COOP RAW 123"
        amount, account_label, tx_type = -12.5, "Conto", "expense"
        category, subcategory, context = "Alimentari", "Spesa", ""
        category_source, to_review, human_validated = "llm", False, False

    keys = list(rp.build_ledger_row(_Tx(), True, {}))
    assert keys.index(rp.COL_RAW) == keys.index(rp.COL_DESC) + 1


def test_every_registry_column_explains_itself():
    """Founder, 2026-10-21: the user cannot guess that a tick on the first
    column writes a rule. Every editable or symbolic column of the Registry
    grid carries a help text on its header."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ui" / "registry_page.py").read_text(encoding="utf-8")
    block = source[source.index("_col_cfg: dict = {"):source.index("edited_df = st.data_editor(")]
    for col in ("COL_SEL", "COL_DATE", "COL_DESC", "COL_INCOME", "COL_EXPENSE", "COL_CAT_SUB", "COL_CONTEXT", "COL_SOURCE", "COL_FLAG_REVIEW",
                "COL_FLAG_VALID", "COL_FLAG_TRANSFER", "COL_VALIDATED", "COL_TRANSFER", "COL_RAW"):
        start = block.index(col)
        end = block.find("COL_", start + len(col) + 2)
        assert "help=" in block[start:end if end > 0 else None], col


def test_registry_column_order():
    """Founder, 2026-10-22: what you read, what you decide, the rule tick,
    then the symbols that only inform; no account or type column."""
    from ui import registry_page as rp

    class _Tx:
        id, date, description, raw_description = "abc", "2026-08-28", "Coop", ""
        amount, account_label, tx_type = -12.5, "Conto", "expense"
        category, subcategory, context = "Alimentari", "Spesa", ""
        category_source, to_review, human_validated = "llm", False, False

    assert list(rp.build_ledger_row(_Tx(), False, {})) == [
        rp.COL_ID, rp.COL_DATE, rp.COL_DESC, rp.COL_INCOME, rp.COL_EXPENSE,
        rp.COL_CAT_SUB, rp.COL_CONTEXT, rp.COL_VALIDATED, rp.COL_TRANSFER, rp.COL_SEL,
        rp.COL_SOURCE, rp.COL_FLAG_REVIEW, rp.COL_FLAG_VALID, rp.COL_FLAG_TRANSFER,
    ]
