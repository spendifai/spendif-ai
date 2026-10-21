"""The product's proposal is kept on the movement, whatever the user decides.

AI human feedback (2026-10-21): comparing the proposal with the category the
user decides says whether the model was right. A log missed the most
frequent decision (a rule applied to the movements already imported).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import CategoryCorrection, Transaction, create_tables, get_session
from db.repository import update_transaction_category


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))


def _tx(s, tx_id, source, category="Spesa"):
    s.add(Transaction(id=tx_id, date="2026-08-01", amount=Decimal("-5"), description="X",
                      source_file="f", account_label="a", tx_type="expense",
                      category=category, subcategory="", category_source=source,
                      category_model="gemma" if source == "llm" else None,
                      model_category=category if source != "rule" else None,
                      model_subcategory="", model_confidence="medium"))


def test_the_proposal_survives_the_user_decision(engine):
    with get_session(engine) as s:
        _tx(s, "t", "llm")
        s.commit()
        update_transaction_category(s, "t", "Ristorazione", "", origin="ledger", source="rule")
        s.commit()
        tx = s.get(Transaction, "t")
        assert (tx.category, tx.category_source) == ("Ristorazione", "rule")
        assert (tx.model_category, tx.model_confidence, tx.category_model) == ("Spesa", "medium", "gemma")


def test_a_correction_of_the_merchant_list_is_logged(engine):
    with get_session(engine) as s:
        _tx(s, "t", "nsi")
        s.commit()
        update_transaction_category(s, "t", "Ristorazione", "", origin="ledger")
        s.commit()
        assert s.query(CategoryCorrection).count() == 1


def test_the_import_keeps_the_proposal_only_for_automatic_sources(monkeypatch):
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
    w.writerow(["03/01/2026", "ESSELUNGA CORSICO", "-40,00"])
    w.writerow(["04/01/2026", "BAR ROSSI", "-4,00"])
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="amex", confidence=Confidence.high,
        confidence_score=1.0,
    )

    def _categorize(*args, **kw):
        txs = kw.get("transactions", args[0] if args else [])
        return [CategorizationResult("Spesa", "Super", Confidence.low, CategorySource.llm)
                if "ESSELUNGA" in t["raw_description"] else
                CategorizationResult("Ristorazione", "Bar", Confidence.high, CategorySource.rule)
                for t in txs]

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", lambda txs, **kw: txs)
    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)
    result = process_file(buf.getvalue().encode("utf-8"), "amex.csv", ProcessingConfig(),
                          _TAXONOMY, [])
    by_desc = {t["raw_description"]: t for t in result.transactions}
    assert (by_desc["ESSELUNGA CORSICO"]["model_category"],
            by_desc["ESSELUNGA CORSICO"]["model_confidence"]) == ("Spesa", "low")
    assert by_desc["BAR ROSSI"].get("model_category") is None       # a rule decided
