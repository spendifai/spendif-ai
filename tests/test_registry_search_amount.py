"""The registry search finds amounts too (spec 21, 7bis): a search text that
is an amount also matches the movements of that amount, with either sign;
without cents it is the whole euro. A text stays a text."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Base, Transaction, get_session
from db.repository import delete_transactions_by_filter, get_transactions, search_amount_range


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with get_session(eng) as s:
        for tx_id, desc, amount in [
            ("out2340", "ESSELUNGA", "-23.40"),
            ("in2340", "RIMBORSO", "23.40"),
            ("out2300", "BAR", "-23.00"),
            ("out2399", "FARMACIA", "-23.99"),
            ("out2400", "EDICOLA", "-24.00"),
            ("big", "IKEA", "-1234.50"),
            ("named", "NEGOZIO 23", "-5.00"),
        ]:
            s.add(Transaction(id=tx_id, date="2026-10-20", description=desc, amount=Decimal(amount),
                              currency="EUR", tx_type="expense", source_file="f", account_label="Conto"))
        s.commit()
        yield s


def _found(session, text):
    return {t.id for t in get_transactions(session, filters={"description": text})}


@pytest.mark.parametrize("text, span", [
    ("23,40", ("23.40", "23.40")), ("23.40", ("23.40", "23.40")), ("-23,40", ("23.40", "23.40")),
    ("23,4", ("23.40", "23.40")), ("1.234,50", ("1234.50", "1234.50")), ("1234,50", ("1234.50", "1234.50")),
    ("23", ("23", "23.99")), (" 23 ", ("23", "23.99")),
])
def test_an_amount_is_read(text, span):
    assert search_amount_range(text) == (Decimal(span[0]), Decimal(span[1]))


@pytest.mark.parametrize("text", ["esselunga", "23 e 40", "23,405", "", "€"])
def test_a_text_is_not_an_amount(text):
    assert search_amount_range(text) is None


def test_an_amount_with_cents_finds_it_with_either_sign(session):
    assert _found(session, "23,40") == {"out2340", "in2340"}
    assert _found(session, "-23.40") == {"out2340", "in2340"}


def test_without_cents_the_whole_euro_and_the_descriptions_with_it(session):
    assert _found(session, "23") == {"out2340", "in2340", "out2300", "out2399", "named"}


def test_thousands_are_read(session):
    assert _found(session, "1.234,50") == {"big"}


def test_a_text_stays_a_text(session):
    assert _found(session, "essel") == {"out2340"}


def test_a_delete_by_filter_removes_what_the_search_shows(session):
    shown = _found(session, "23,40")
    assert delete_transactions_by_filter(session, {"description": "23,40"}) == len(shown)
    assert _found(session, "23,40") == set()
    assert _found(session, "23") == {"out2300", "out2399", "named"}
