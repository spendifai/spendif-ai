"""Tests for the landing period: service layer and the Home "last loaded" preset."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from services.guided_flow_service import GuidedFlowService
from services.transaction_service import TransactionService
from ui.home_page import _PRESET_LAST_LOADED, _resolve_period

TODAY = date(2026, 10, 12)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _seed(engine, dates):
    with get_session(engine) as s:
        for i, d in enumerate(dates):
            s.add(Transaction(
                id=f"tx{i}", date=d, amount=Decimal(-10), currency="EUR",
                description="x", source_file="f.csv", doc_type="bank_statement",
                account_label="BancaX", tx_type="expense", to_review=False,
            ))
        s.commit()


def test_last_movement_date_empty_db(engine):
    assert TransactionService(engine).get_last_movement_date() is None


def test_last_movement_date_is_the_most_recent(engine):
    _seed(engine, ["2026-03-02", "2026-08-14", "2026-05-30"])
    assert TransactionService(engine).get_last_movement_date() == date(2026, 8, 14)


def test_landing_period_empty_db(engine):
    assert GuidedFlowService(engine).landing_period() is None


def test_landing_period_ends_with_last_movement_month(engine):
    _seed(engine, ["2026-03-02", "2026-08-14"])
    assert GuidedFlowService(engine).landing_period() == (date(2026, 6, 1), date(2026, 8, 31))


def test_stopped_at_when_last_month_is_missing(engine):
    _seed(engine, ["2026-08-14"])
    assert GuidedFlowService(engine).stopped_at(TODAY) == date(2026, 8, 14)


def test_not_stopped_when_last_month_is_loaded(engine):
    _seed(engine, ["2026-09-28"])
    assert GuidedFlowService(engine).stopped_at(TODAY) is None


def test_not_stopped_on_empty_db(engine):
    assert GuidedFlowService(engine).stopped_at(TODAY) is None


def test_home_last_loaded_preset_uses_landing():
    landing = (date(2026, 6, 1), date(2026, 8, 31))
    assert _resolve_period(_PRESET_LAST_LOADED, today=TODAY, landing=landing) == landing


def test_home_last_loaded_preset_without_data_falls_back_to_12_months():
    assert _resolve_period(_PRESET_LAST_LOADED, today=TODAY) == (date(2025, 10, 12), TODAY)
