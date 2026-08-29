"""Gate a GUI disegnata: ogni etichetta che l'utente legge deve venire dai
file di traduzione, non dal codice.

Il controllo statico (test_no_hardcoded_labels) guarda il sorgente e non vede
le etichette che arrivano per altre strade. Qui invece le pagine vengono
davvero renderizzate con AppTest, nelle due lingue, e si guarda cosa esce.

Regola: l'etichetta di un widget deve corrispondere a una stringa del catalogo
della lingua attiva, oppure essere un dato (una categoria, un conto, un
contesto: quelli vengono dal database dell'utente e non si traducono).

ESCLUSA: ui/onboarding_page.py. Il wizard gira PRIMA che l'utente scelga la
lingua, quindi la rileva dal browser e usa un proprio catalogo minimo che
copre cinque lingue (it, en, fr, de, es) contro le due dei file i18n. Le sue
etichette sono tradotte, ma in una seconda fonte di verita': unificarla
significa decidere che fare di francese, tedesco e spagnolo, ed e' tracciato
a parte invece di essere nascosto sotto un'esenzione muta.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
I18N_DIR = REPO / "ui" / "i18n"
PLACEHOLDER = re.compile(r"\{[^}]*\}")

# widget la cui etichetta e' cromatura dell'interfaccia, mai un dato
WIDGET_CHROME = ("button", "checkbox", "selectbox", "multiselect", "radio",
                 "text_input", "number_input", "toggle", "expander")


def _catalogo(lang: str) -> list[re.Pattern]:
    """Ogni traduzione diventa un pattern: i {placeholder} sono jolly."""
    valori = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")).values()
    pattern = []
    for v in valori:
        if not isinstance(v, str) or not v.strip():
            continue
        pezzi = [re.escape(p) for p in PLACEHOLDER.split(v)]
        pattern.append(re.compile(r"\s*" + r".*".join(pezzi) + r"\s*", re.S))
    return pattern


def _render(page: str, lang: str, db_path: str):
    from streamlit.testing.v1 import AppTest

    def script():
        import os
        import sys
        sys.path.insert(0, os.environ["SPENDIFAI_REPO"])
        import db.models as M
        from ui.i18n import set_language
        import importlib

        lingua = os.environ["SPENDIFAI_TEST_LANG"]
        set_language(lingua)
        engine = M.get_engine(os.environ["SPENDIFAI_DB"])
        # alcune pagine rileggono la lingua dalle impostazioni invece che dallo
        # stato i18n: se le due sorgenti divergono il test misura il proprio
        # disallineamento, non un difetto del prodotto
        sessione = M.get_session(engine)
        sessione.merge(M.UserSettings(key="ui_language", value=lingua))
        sessione.commit()
        modulo, funzione = os.environ["SPENDIFAI_TEST_PAGE"].split(":")
        pagina = getattr(importlib.import_module(modulo), funzione)
        pagina(engine)

    os.environ["SPENDIFAI_REPO"] = str(REPO)
    os.environ["SPENDIFAI_DB"] = f"sqlite:///{db_path}"
    os.environ["SPENDIFAI_TEST_LANG"] = lang
    os.environ["SPENDIFAI_TEST_PAGE"] = page
    return AppTest.from_function(script, default_timeout=120).run()


def _etichette(at) -> list[tuple[str, str]]:
    fuori = []
    for kind in WIDGET_CHROME:
        try:
            elementi = list(getattr(at, kind))
        except Exception:          # widget non presente in questa versione
            continue
        for el in elementi:
            label = getattr(el, "label", None)
            if isinstance(label, str) and label.strip():
                fuori.append((kind, label))
    return fuori


_MARKUP = re.compile(r"[*_`#]|\([\d/]+\)|[☑☐✅⚠️·—–-]")


def _e_dato(testo: str, valori_dati: set[str]) -> bool:
    """Vero se l'etichetta e' costruita attorno a un dato dell'utente.

    Il filtro categorie disegna expander tipo "☑ **Alimentari** (4/4)": il nome
    della categoria arriva dal database e non si traduce, il resto e' decorazione.
    """
    nudo = _MARKUP.sub("", testo).strip()
    if nudo in valori_dati:
        return True
    return any(v and v in testo for v in valori_dati)


@pytest.fixture(scope="module")
def db_demo(tmp_path_factory):
    """Database minimo: un conto, due transazioni, tassonomia di default."""
    import sys
    sys.path.insert(0, str(REPO))
    db = tmp_path_factory.mktemp("gui") / "gui_demo.db"
    os.environ["SPENDIFAI_DB"] = f"sqlite:///{db}"
    import db.models as M

    engine = M.get_engine(f"sqlite:///{db}")
    M.create_tables(engine)
    s = M.get_session(engine)
    s.add(M.Account(name="Conto test", bank_name="Banca", account_type="bank_account"))
    for i, (amount, cat, sub) in enumerate(
        [(-42.0, "Alimentari", "Spesa supermercato"), (1500.0, "Lavoro dipendente", "Stipendio")]
    ):
        s.add(M.Transaction(
            id=f"gui{i}", date="2026-08-01", amount=amount, description=f"MOVIMENTO {i}",
            account_label="Conto test", tx_type="expense" if amount < 0 else "income",
            category=cat, subcategory=sub,
        ))
    # una regola: senza, la pagina Regole non disegna il blocco di modifica e il
    # gate non vedrebbe mai quelle etichette
    s.add(M.CategoryRule(pattern="ESSELUNGA", match_type="contains",
                         category="Alimentari", subcategory="Spesa supermercato", priority=10))
    for k, v in {"onboarding_done": "true", "ui_language": "it", "owner_names": "Test",
                 "llm_backend": "local_llama_cpp"}.items():
        s.merge(M.UserSettings(key=k, value=v))
    s.commit()
    return str(db)


@pytest.fixture(scope="module")
def valori_dati(db_demo) -> set[str]:
    """Categorie, sottocategorie, conti, contesti: dati, non etichette."""
    import sqlite3

    con = sqlite3.connect(db_demo)
    out: set[str] = set()
    for q in ("SELECT name FROM taxonomy_category",
              "SELECT name FROM taxonomy_subcategory",
              "SELECT name FROM account",
              "SELECT DISTINCT context FROM 'transaction' WHERE context IS NOT NULL"):
        try:
            out |= {r[0] for r in con.execute(q) if r[0]}
        except sqlite3.Error:
            pass
    con.close()
    return out


@pytest.mark.parametrize("lang", ["it", "en"])
@pytest.mark.parametrize("page", [
    "ui.home_page:render_home_page",
    "ui.registry_page:render_registry_page",
    "ui.review_page:render_review_page",
    "ui.upload_page:render_upload_page",
    "ui.rules_page:render_rules_page",
    "ui.taxonomy_page:render_taxonomy_page",
    "ui.settings_page:render_settings_page",
    "ui.budget_page:render_budget_page",
    "ui.history_page:render_history_page",
    "ui.checklist_page:render_checklist_page",
    "ui.analysis_page:render_analysis_page",
    "ui.budget_vs_actual_page:render_budget_vs_actual_page",
    "ui.bulk_edit_page:render_bulk_edit_page",
    "ui.chat_page:render_chat_page",
    "ui.counterparts_page:render_counterparts_page",
    "ui.llm_models_page:render_llm_models_page",
    "ui.report_page:render_report_page",
])
def test_gui_labels_come_from_the_catalogue(page, lang, db_demo, valori_dati):
    at = _render(page, lang, db_demo)
    assert not at.exception, f"{page} [{lang}] ha sollevato: {at.exception[0].message[:300]}"

    catalogo = _catalogo(lang)
    intruse = []
    for kind, label in _etichette(at):
        testo = label.strip()
        if _e_dato(testo, valori_dati) or len(re.sub(r"[^A-Za-zÀ-ÿ]", "", testo)) < 4:
            continue
        if any(p.fullmatch(testo) for p in catalogo):
            continue
        intruse.append(f"{kind}: {testo[:70]!r}")
    assert not intruse, (
        f"{page} [{lang}]: etichette non presenti nel catalogo {lang}.json\n  "
        + "\n  ".join(intruse)
    )
