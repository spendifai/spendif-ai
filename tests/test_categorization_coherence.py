"""One counterpart, one category: the model is asked once per counterpart.

Spec: documents/04_software_engineering/22_coerenza_categorizzazione.md.
Founder's case (2026-10-20): CLIVATI 1969 S R L in an Amex statement and
again in a Satispay one went back to the model each time.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core import categorizer
from core.categorizer import CategorizationResult, CategoryRule, TaxonomyConfig, categorize_batch
from core.models import CategorySource, Confidence
from core.rule_key import exact_key
from db.models import Transaction, create_tables, get_session
from db.repository import known_counterpart_categories

_TAX = TaxonomyConfig(expenses={"Ristorazione": ["Bar"], "Spesa": ["Supermercato"]},
                      income={"Entrate": ["Rimborsi"]})


@pytest.fixture
def model(monkeypatch):
    """A fake model that records what it is asked and answers Ristorazione."""
    asked: list[str] = []

    def _fake(transactions, indices, results, *args, **kwargs):
        for i in indices:
            asked.append(transactions[i]["description"])
            results[i] = CategorizationResult("Ristorazione", "Bar", Confidence.high,
                                              CategorySource.llm)

    monkeypatch.setattr(categorizer, "_run_llm_batch_group", _fake)
    return asked


def _tx(desc, amount=-10, raw=None):
    return {"id": desc + str(amount), "description": desc, "amount": amount,
            "raw_description": raw or desc, "doc_type": "credit_card"}


def test_a_counterpart_already_in_the_ledger_skips_the_model(model):
    known = {(exact_key("CLIVATI 1969 S R L"), "out"): ("Spesa", "Supermercato")}
    [r] = categorize_batch([_tx("Clivati 1969 S.R.L.")], _TAX, [], llm_backend=object(),
                           known_categories=known)
    assert (r.category, r.subcategory, r.source) == ("Spesa", "Supermercato", CategorySource.history)
    assert model == []


def test_another_direction_is_another_counterpart(model):
    known = {(exact_key("CLIVATI"), "out"): ("Spesa", "Supermercato")}
    [r] = categorize_batch([_tx("CLIVATI", amount=10)], _TAX, [], llm_backend=object(),
                           known_categories=known)
    assert r.source == CategorySource.llm and model == ["CLIVATI"]


def test_rules_still_come_first(model):
    rule = CategoryRule(id=1, pattern="CLIVATI", match_type="starts_with", category="Ristorazione",
                        subcategory="Bar", doc_type=None, priority=10)
    known = {(exact_key("CLIVATI 1969"), "out"): ("Spesa", "Supermercato")}
    [r] = categorize_batch([_tx("CLIVATI 1969")], _TAX, [rule], llm_backend=object(),
                           known_categories=known)
    assert (r.category, r.source) == ("Ristorazione", CategorySource.rule)


def test_the_model_sees_one_movement_per_counterpart(model):
    txs = [_tx("BAR ROSSI", -2), _tx("bar rossi", -3), _tx("BAR*ROSSI", -4), _tx("ESSELUNGA", -50)]
    res = categorize_batch(txs, _TAX, [], llm_backend=object())
    assert sorted(model) == ["BAR ROSSI", "ESSELUNGA"]
    assert all(r.category == "Ristorazione" for r in res)


# ── From the ledger ──────────────────────────────────────────────────────────

def test_the_ledger_gives_the_most_frequent_category_without_hand_choices(tmp_path):
    eng = create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))
    rows = [("a", "CLIVATI 1969 S R L", -5, "Ristorazione", "llm"),
            ("b", "CLIVATI 1969 S R L", -6, "Ristorazione", "rule"),
            ("c", "CLIVATI 1969 S.R.L.", -7, "Spesa", "llm"),
            ("d", "CLIVATI 1969 S R L", -8, "Lavoro", "manual"),     # one-off: not counted
            ("e", "CLIVATI 1969 S R L", -8, "Lavoro", "manual"),
            ("f", "CLIVATI 1969 S R L", 9, "Entrate", "llm")]       # income: another key
    with get_session(eng) as s:
        for tx_id, desc, amt, cat, src in rows:
            s.add(Transaction(id=tx_id, date="2026-01-01", amount=Decimal(str(amt)), description=desc,
                              source_file="f", account_label="amex", tx_type="card_tx",
                              category=cat, subcategory="", category_source=src))
        s.commit()
        known = known_counterpart_categories(s)
    key = exact_key("CLIVATI 1969 S R L")
    assert known[(key, "out")] == ("Ristorazione", "")
    assert known[(key, "in")] == ("Entrate", "")


def test_a_reused_category_is_not_a_decision_of_the_user():
    from db.models import USER_CATEGORY_SOURCES
    assert CategorySource.history.value not in USER_CATEGORY_SOURCES


# ── The raw signature (spec 22) ───────────────────────────────────────

def test_amount_date_and_codes_do_not_change_the_signature():
    from core.normalizer import raw_signature
    a = "Pagam. POS - PAGAMENTO POS 8,80 EUR DEL 01.10.2026 A (ITA) PELLEGRINI SPA CARTA ***7746 CAU 98105"
    b = "Pagam. POS - PAGAMENTO POS 12,30 EUR DEL 15.11.2026 A (ITA) Pellegrini S.p.A CARTA ***7746 CAU 98105"
    assert raw_signature(a) == raw_signature(b) == "pagam pos pagamento pos eur del a ita pellegrini spa carta cau"


@pytest.mark.parametrize("raw", ["SALVADANAIO", "LIDL 1423", "PAGAMENTO 0012345", "12345", "", None])
def test_one_word_is_no_signature(raw):
    """Founder, 2026-10-20: a single word is too generic, a loss but safer."""
    from core.normalizer import raw_signature
    assert raw_signature(raw) == ""


def _ledger(tmp_path, rows):
    from datetime import datetime, timedelta
    eng = create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))
    t0 = datetime(2026, 1, 1)
    with get_session(eng) as s:
        for i, (raw, desc, tx_type) in enumerate(rows):
            s.add(Transaction(id=f"t{i}", date="2026-01-01", amount=Decimal("-5"), description=desc,
                              raw_description=raw, source_file="f", account_label="amex",
                              tx_type=tx_type, created_at=t0 + timedelta(minutes=i)))
        s.commit()
    return eng


def test_one_signature_one_counterpart_the_oldest(tmp_path):
    from db.repository import known_signatures
    eng = _ledger(tmp_path, [("Lidl Italia 1423 Milano", "Lidl", "card_tx"),
                             ("Lidl Italia 1500 Milano", "Lidl 1500", "card_tx"),
                             ("Bonifico A Rossi Mario 99", "Rossi Mario", "internal_out"),
                             ("Esselunga Spa", "", "card_tx")])
    with get_session(eng) as s:
        known = known_signatures(s)
    assert known == {"lidl italia milano": "Lidl"}


def test_the_import_reuses_known_and_same_file_signatures(monkeypatch):
    import csv
    import io

    from core import orchestrator
    from core.models import DocumentType, SignConvention
    from core.orchestrator import ProcessingConfig, process_file
    from core.schemas import DocumentSchema
    from tests.test_giroconto_rule import _TAXONOMY

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    w.writerow(["03/01/2026", "LIDL ITALIA 1423 MILANO", "-4,00"])        # known
    w.writerow(["04/01/2026", "ESSELUNGA CORSICO 0012", "-40,00"])        # first of its signature
    w.writerow(["05/01/2026", "ESSELUNGA CORSICO 0099", "-30,00"])        # same signature
    w.writerow(["06/01/2026", "SALVADANAIO", "-10,00"])                   # one word: no signature
    w.writerow(["07/01/2026", "SALVADANAIO", "-11,00"])
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="amex", confidence=Confidence.high,
        confidence_score=1.0,
    )
    sent: list[str] = []

    def _clean(txs, **kw):
        sent.extend(t["raw_description"] for t in txs)
        for t in txs:
            t["description"] = "MODEL " + t["raw_description"][-4:]
        return txs

    def _categorize(*args, **kw):
        txs = kw.get("transactions", args[0] if args else [])
        return [CategorizationResult("Altro", "", Confidence.high, CategorySource.llm) for _ in txs]

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", _clean)
    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)

    result = process_file(buf.getvalue().encode("utf-8"), "amex.csv", ProcessingConfig(),
                          _TAXONOMY, [], known_signatures={"lidl italia milano": "LIDL"})
    by_raw = {t["raw_description"]: t["description"] for t in result.transactions}
    assert by_raw["LIDL ITALIA 1423 MILANO"] == "LIDL"
    assert by_raw["ESSELUNGA CORSICO 0012"] == by_raw["ESSELUNGA CORSICO 0099"] == "MODEL 0012"
    assert sorted(sent) == ["ESSELUNGA CORSICO 0012", "SALVADANAIO", "SALVADANAIO"]
