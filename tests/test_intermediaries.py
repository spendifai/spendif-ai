"""Payment intermediaries: the counterpart is the merchant after the asterisk.

Spec: documents/04_software_engineering/20_intermediari_di_pagamento.md.
Founder's data (2026-10-20): of 41 PayPal movements the model kept "PAYPAL"
in 9 and "PAYPAL *X" in 6; EASYPARK came out three ways.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.intermediaries import is_dirty, learn, merchant_after, prefix_key
from db.models import Transaction, create_tables, get_session
from services.intermediary_service import IntermediaryService, seed_prefixes

ACTIVE = {"PAYPAL", "CKO", "3CPAYMENT"}


# ── Pure functions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, merchant", [
    ("PAYPAL *TRENORD SRL     0272494949 PIAZZALE LUIGI CADORNA 14", "TRENORD SRL"),
    ("PAYPAL *DALES SHELLYITA 0833726014", "DALES SHELLYITA"),
    ("PAYPAL *LUMEN INC       8457343258", "LUMEN INC"),
    ("CKO*FEVER 522725        MADRID", "FEVER"),
    ("3CPAYMENT*RADISSON BLU HOTEL  123", "RADISSON BLU HOTEL"),
    ("GOOGLE*CLOUD 3CL96J GOO CC", None),                 # not an intermediary
    ("AMZN MKTP IT*TE3GN9YL4  800-279-6620", None),       # not an intermediary
    ("PAYPAL *12345678", None),                           # nothing that names a merchant
    ("ESSELUNGA CORSICO", None),
    # Not only at the start, not only after an asterisk (founder, 2026-10-20)
    ("6525970876 ALTRE SPESE PAYPAL *FATTURA24", "FATTURA24"),                     # Nexi
    ("Pagam. POS - PAGAMENTO POS 10,00 EUR DEL 23.02.2026 A (LUX) PAYPAL  GONEL.SILVIA"
     "               CARTA  ***7746 CAU 98105", "GONEL SILVIA"),                      # Mediobanca
    ("Pagam. POS - PAGAMENTO POS 86,65 EUR DEL 26.04.2026 A (HKG) PAYPAL  KLOOKTRAVEL"
     "                CARTA  ***7746 CAU 98105", "KLOOKTRAVEL"),
    ("Pagam. POS - PAGAMENTO POS 2,10 EUR DEL 05.05.2026 A (LUX) PAYPAL  X39 34987844"
     "                CARTA  ***7746 CAU 98105", None),                             # a code, no name
    # The merchant field is at most 25 characters; a code is not a merchant
    ("3CPAYMENT*RADISSON HOTE OPFIKON Foreign Spend Amount: 365.06 Franchi Svizzeri",
     "RADISSON HOTE OPFIKON"),
    ("PAYPAL *P33C6E454D      12345", None),
    ("PAYPAL *HRB 123456", "HRB"),
    # A single space: PayPal is the counterpart itself (direct debit)
    ("Addebito SDD - PayPal Europe S.a.r.-1047494877140/PAYPAL"
     "                                                           -N.  ", None),
    ("Addebito SDD - PayPal (Europe) S.a -1042757026663 PAYPAL"
     "                                                           -N.  ", None),
])
def test_merchant_after(raw, merchant):
    assert merchant_after(raw, ACTIVE) == merchant


def test_learning_counts_different_names_not_movements():
    texts = ["PAYPAL *TRENORD x"] * 10 + ["PAYPAL *EASYPARK y",
             "GOOGLE*CLOUD a", "GOOGLE*CLOUD b",                 # always the same name
             "AMZN MKTP IT*TE3GN9YL4", "AMZN MKTP IT*1Q5OU7ND5",  # order codes
             "SUMUP *BAR ROSSI"]                                   # one name only
    assert {p: sorted(n) for p, n in learn(texts).items()} == {"PAYPAL": ["EASYPARK", "TRENORD"]}


def test_a_mediobanca_description_saved_with_the_intermediary_is_dirty():
    raw = "Pagam. POS - PAGAMENTO POS 10,00 EUR DEL 23.02.2026 A (LUX) PAYPAL  GONEL.SILVIA      CARTA"
    assert is_dirty("PAYPAL GONEL.SILVIA", raw, ACTIVE) == "GONEL SILVIA"
    sdd = "Addebito SDD - PayPal Europe S.a.r.-1047494877140/PAYPAL      -N.  "
    assert is_dirty("PAYPAL EUROPE S.A.R.", sdd, ACTIVE) is None       # PayPal itself


def test_dirty_descriptions_and_the_ones_the_user_changed():
    raw = "PAYPAL *EASYPARK        0852226737"
    assert is_dirty("PAYPAL", raw, ACTIVE) == "EASYPARK"
    assert is_dirty("PAYPAL *EASYPARK", raw, ACTIVE) == "EASYPARK"
    assert is_dirty("Paypal Easypark", raw, ACTIVE) == "EASYPARK"
    assert is_dirty("Parcheggi Milano", raw, ACTIVE) is None       # changed by the user
    assert is_dirty("PAYPAL", raw, {"SUMUP"}) is None               # not active


def test_the_seed_holds_the_transparent_ones_and_not_satispay():
    seed = seed_prefixes()
    assert {"PAYPAL", "SUMUP", "STRIPE", "SQ", "CKO", "3CPAYMENT"} <= set(seed)
    assert prefix_key("Satispay") not in seed


# ── Table and saved movements ────────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))


def _tx(s, tx_id, raw, desc, source="llm", category="Altro"):
    s.add(Transaction(id=tx_id, date="2025-01-10", amount=Decimal("-5"), description=desc,
                      raw_description=raw, source_file="f.xlsx", account_label="amex",
                      tx_type="card_tx", category=category, subcategory="",
                      category_source=source))


def test_seed_sync_adds_never_removes_and_never_turns_back_on(engine):
    svc = IntermediaryService(engine)
    assert set(svc.sync_seed()) == set(seed_prefixes())
    svc.set_active("PAYPAL", False)
    svc.add_user("Mio Gestore")
    assert svc.sync_seed() == []                                    # nothing new
    rows = {r["prefix"]: r for r in svc.page_rows()}
    assert rows["PAYPAL"]["active"] is False                       # stays off
    assert rows["MIO GESTORE"]["origin"] == "user"


def test_a_new_intermediary_rewrites_saved_descriptions_and_applies_rules(engine):
    from services.rule_service import RuleService
    RuleService(engine).create_rule(pattern="Easypark", match_type="exact",
                                    category="Trasporti", subcategory="", priority=10)
    with get_session(engine) as s:
        _tx(s, "a", "PAYPAL *EASYPARK        0852226737", "Paypal")
        _tx(s, "b", "PAYPAL *EASYPARK        0852226737", "PAYPAL *EASYPARK")
        _tx(s, "c", "PAYPAL *EASYPARK        0852226737", "Parcheggi Milano")   # the user's
        _tx(s, "d", "PAYPAL *EASYPARK        0852226737", "Paypal", source="manual",
            category="Lavoro")
        s.commit()
    IntermediaryService(engine).sync_seed()
    with get_session(engine) as s:
        got = {i: (s.get(Transaction, i).description, s.get(Transaction, i).category)
               for i in "abcd"}
    assert got == {
        "a": ("Easypark", "Trasporti"),
        "b": ("Easypark", "Trasporti"),
        "c": ("Parcheggi Milano", "Altro"),          # changed by the user: untouched
        "d": ("Easypark", "Lavoro"),                 # merchant written, hand choice kept
    }


def test_learning_from_the_files_of_an_import(engine):
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Data", "Descrizione", "Importo"])
    ws.append(["01/02/2026", "NUOVOPAY *BARETTO ROSSI 123456", "-3"])
    ws.append(["02/02/2026", "NUOVOPAY *LIBRERIA VERDI 654321", "-20"])
    buf = io.BytesIO()
    wb.save(buf)
    added = IntermediaryService(engine).learn_from_files([("a.xlsx", buf.getvalue())])
    assert added == ["NUOVOPAY"]
    assert "NUOVOPAY" in IntermediaryService(engine).active()


def test_rules_written_on_an_intermediary_are_flagged(engine):
    from services.rule_service import RuleService
    IntermediaryService(engine).sync_seed()
    rs = RuleService(engine)
    rs.create_rule(pattern="PAYPAL ITALOTRENO", match_type="exact", category="Trasporti",
                   subcategory="", priority=10)
    rs.create_rule(pattern="TRENORD", match_type="starts_with", category="Trasporti",
                   subcategory="", priority=10)
    flagged = IntermediaryService(engine).rules_on_intermediaries(rs.get_rules())
    assert [r.pattern for r in flagged] == ["PAYPAL ITALOTRENO"]


# ── In the import: no model for what the table knows ─────────────────────────

def test_the_import_writes_the_merchant_and_sends_only_the_rest_to_the_model(monkeypatch):
    import csv
    import io
    from core import orchestrator
    from core.categorizer import CategorizationResult
    from core.models import CategorySource, Confidence, DocumentType, SignConvention
    from core.orchestrator import ProcessingConfig, process_file
    from core.schemas import DocumentSchema
    from tests.test_giroconto_rule import _TAXONOMY

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    w.writerow(["03/01/2026", "PAYPAL *TRENORD SRL     0272494949", "-4,00"])
    w.writerow(["04/01/2026", "ESSELUNGA CORSICO", "-40,00"])
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="amex", confidence=Confidence.high,
        confidence_score=1.0,
    )
    sent_to_cleaner: list[str] = []

    def _clean(txs, **kw):
        sent_to_cleaner.extend(t["raw_description"] for t in txs)
        for t in txs:
            t["description"] = "CLEANED " + t["raw_description"][:9]
        return txs

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", _clean)
    def _categorize(*args, **kw):
        txs = kw.get("transactions", args[0] if args else [])
        return [CategorizationResult(category="Altro", subcategory="", confidence=Confidence.high,
                                     source=CategorySource.llm) for _ in txs]

    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)

    result = process_file(buf.getvalue().encode("utf-8"), "amex.csv", ProcessingConfig(),
                          _TAXONOMY, [], intermediary_prefixes={"PAYPAL"})
    by_raw = {t["raw_description"]: t["description"] for t in result.transactions}
    assert by_raw["PAYPAL *TRENORD SRL     0272494949"] == "TRENORD SRL"
    assert by_raw["ESSELUNGA CORSICO"] == "CLEANED ESSELUNGA"
    assert sent_to_cleaner == ["ESSELUNGA CORSICO"]                  # PayPal never sent


# ── The page ─────────────────────────────────────────────────────────────────

def _page(db_path: str) -> None:
    from sqlalchemy import create_engine
    from ui.i18n import set_language
    from ui.intermediaries_page import render_intermediaries_page
    set_language("en")
    render_intermediaries_page(create_engine(f"sqlite:///{db_path}"))


def test_the_page_lists_and_adds(tmp_path):
    from streamlit.testing.v1 import AppTest
    from ui.i18n import get_language, set_language
    before = get_language()
    db_path = tmp_path / "l.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    IntermediaryService(eng).sync_seed()
    try:
        app = AppTest.from_function(_page, kwargs={"db_path": str(db_path)}).run(timeout=30)
        assert not app.exception, app.exception
        assert "PAYPAL" in list(app.dataframe[0].value.iloc[:, 0]) if app.dataframe else True
        app.text_input(key="intermediaries_new").set_value("Nuovo Pay").run(timeout=30)
        [b for b in app.button if b.key == "intermediaries_add"][0].click().run(timeout=30)
        assert not app.exception, app.exception
        assert "NUOVO PAY" in IntermediaryService(eng).active()
    finally:
        set_language(before)
