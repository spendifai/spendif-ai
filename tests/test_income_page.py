"""Income step: a counterpart that also has spending gets its category on
its income movements only, never through a rule that would change the
spending too."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from services.rule_service import RuleService
from services.transaction_service import TransactionService
from ui.counterparts_page import _apply_to_income


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    with get_session(eng) as s:
        for tx_id, amount, tx_type, cat in [
            ("in1", 40, "income", "Altro entrate"),
            ("out1", -90, "expense", "Shopping"),
        ]:
            s.add(Transaction(
                id=tx_id, date=date(2026, 8, 1).isoformat(), amount=Decimal(amount),
                currency="EUR", description="Negozio", source_file="f.csv",
                doc_type="bank_statement", account_label="BancaX", tx_type=tx_type,
                category=cat, category_source="llm", to_review=False,
            ))
        s.commit()
    return eng


def _row(**kw):
    base = {"description": "Negozio", "giroconto": False, "unmark_giroconto": False,
            "new_cat": "Shopping", "new_sub": "", "set_category": True, "confirm": True}
    return {**base, **kw}


def test_category_goes_to_income_movements_only(engine):
    tx = TransactionService(engine)
    assert _apply_to_income(tx, _row()) == 1
    by_id = {t.id: t for t in tx.get_by_rule_pattern("Negozio", "exact")}
    assert by_id["in1"].category == "Shopping"
    assert by_id["in1"].category_source == "manual"
    assert by_id["out1"].category_source == "llm"      # spending untouched
    assert RuleService(engine).get_rules() == []          # no rule


def test_transfer_tick_moves_income_movements_only(engine):
    tx = TransactionService(engine)
    _apply_to_income(tx, _row(giroconto=True, set_category=False))
    by_id = {t.id: t for t in tx.get_by_rule_pattern("Negozio", "exact")}
    assert by_id["in1"].tx_type == "internal_in"
    assert by_id["out1"].tx_type == "expense"
