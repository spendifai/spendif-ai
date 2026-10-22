"""Movements dated in the future are set aside at import, fixed and imported."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.guided_flow import swapped_date
from core.orchestrator import ImportResult
from db import repository
from db.models import HeldMovement, Transaction, create_tables, get_session
from services.held_movement_service import HeldMovementService

TODAY = date.today()
FUTURE = TODAY + timedelta(days=40)
PAST = TODAY - timedelta(days=10)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _tx(id, d, amount="-12.50"):
    return {"id": id, "date": d, "amount": Decimal(amount), "description": f"SHOP {id}",
            "account_label": "A", "tx_type": "expense", "category": "Casa",
            "subcategory": "Varie", "source_file": "a.csv", "raw_description": f"shop {id}"}


def _import(engine, *txs):
    result = ImportResult(batch_sha256="b" * 64, source_name="a.csv", transactions=list(txs),
                          doc_schema=None, reconciliations=[], transfer_links=[])
    with get_session(engine) as s:
        repository.persist_import_result(s, result)
    return result


def _ids(engine, model):
    with get_session(engine) as s:
        return sorted(r.id for r in s.query(model))


@pytest.mark.parametrize("d, today, expected", [
    (date(2026, 12, 2), date(2026, 10, 14), date(2026, 2, 12)),   # swapped: in the past
    (date(2026, 12, 20), date(2026, 10, 14), None),                # day 20 cannot be a month
    (date(2026, 11, 11), date(2026, 10, 14), None),                # same date when swapped
    (date(2026, 12, 11), date(2026, 10, 14), None),                # 12 November: still future
])
def test_swapped_date(d, today, expected):
    assert swapped_date(d, today) == expected


def test_a_future_date_is_set_aside_not_imported(engine):
    result = _import(engine, _tx("ok", PAST), _tx("fut", FUTURE))
    assert result.held_count == 1
    assert _ids(engine, Transaction) == ["ok"]
    assert _ids(engine, HeldMovement) == ["fut"]


def test_fixed_date_imports_the_movement_as_it_was(engine):
    _import(engine, _tx("fut", FUTURE))
    svc = HeldMovementService(engine)
    assert svc.release("fut", PAST) is True
    with get_session(engine) as s:
        tx = s.get(Transaction, "fut")
        assert (tx.date, tx.category, float(tx.amount)) == (PAST.isoformat(), "Casa", -12.5)
    assert svc.held() == []


def test_a_date_still_in_the_future_is_refused(engine):
    _import(engine, _tx("fut", FUTURE))
    with pytest.raises(ValueError):
        HeldMovementService(engine).release("fut", FUTURE)


def test_a_movement_already_in_the_ledger_is_not_inserted_twice(engine):
    _import(engine, _tx("x", PAST))
    with get_session(engine) as s:
        repository.hold_movement(s, _tx("x", FUTURE), batch_id=None, reason="date_future")
        s.commit()
    assert HeldMovementService(engine).release("x", PAST) is False
    assert _ids(engine, Transaction) == ["x"] and _ids(engine, HeldMovement) == []


def test_discard_drops_it(engine):
    _import(engine, _tx("fut", FUTURE))
    HeldMovementService(engine).discard("fut")
    assert _ids(engine, HeldMovement) == [] and _ids(engine, Transaction) == []


def test_future_movements_already_in_the_ledger_are_moved_aside(engine):
    with get_session(engine) as s:
        for id, d in (("old", PAST), ("fut", FUTURE)):
            s.add(Transaction(id=id, date=d.isoformat(), amount=Decimal("-5"), account_label="A",
                              tx_type="expense", description="x", source_file="a.csv"))
        s.commit()
    svc = HeldMovementService(engine)
    assert svc.hold_existing() == 1
    assert _ids(engine, Transaction) == ["old"]
    assert [r.id for r in svc.held()] == ["fut"]
