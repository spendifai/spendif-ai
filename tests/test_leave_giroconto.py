"""A movement that is not a giroconto never stays without a category: back
from giroconto, or left without one by a subcategory that went, it takes the
product's proposal when there is one, otherwise the fallback category of its
direction, and goes to review."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from db.models import Transaction, _migrate_empty_category_to_fallback, create_tables, get_session
from db.repository import (
    bulk_set_giroconto_by_description,
    create_category_rule,
    set_giroconto_by_rule_pattern,
    toggle_transaction_giroconto,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _tx(s, tx_id, amount, tx_type, description="Mario Rossi", category=None):
    s.add(Transaction(id=tx_id, date="2026-10-01", amount=Decimal(amount), currency="EUR",
                      description=description, source_file="f.csv", doc_type="bank_account",
                      account_label="A", tx_type=tx_type, category=category,
                      reconciled=False, to_review=False))
    s.flush()


def _state(s, tx_id):
    tx = s.get(Transaction, tx_id)
    return tx.tx_type, tx.category, tx.subcategory, tx.to_review


def test_one_movement_back_from_giroconto_takes_the_fallback(engine):
    with get_session(engine) as s:
        _tx(s, "out", "-50", "internal_out")
        _tx(s, "in", "80", "internal_in", description="Luigi Bianchi")
        toggle_transaction_giroconto(s, "out")
        toggle_transaction_giroconto(s, "in")
        assert _state(s, "out") == ("expense", "Altro", "Spese non classificate", True)
        assert _state(s, "in") == ("income", "Altro entrate", "Entrate non classificate", True)


def test_every_movement_of_a_counterpart_back_from_giroconto(engine):
    with get_session(engine) as s:
        _tx(s, "a", "-50", "internal_out")
        _tx(s, "b", "-20", "internal_out")
        bulk_set_giroconto_by_description(s, "Mario Rossi", make_giroconto=False)
        assert _state(s, "a")[1] == "Altro" and _state(s, "b")[1] == "Altro"


def test_unmarked_by_rule_pattern(engine):
    with get_session(engine) as s:
        _tx(s, "a", "-50", "internal_out")
        set_giroconto_by_rule_pattern(s, "Mario Rossi", "exact", make_giroconto=False)
        assert _state(s, "a") == ("expense", "Altro", "Spese non classificate", True)


def test_a_category_already_there_stays(engine):
    with get_session(engine) as s:
        _tx(s, "a", "-50", "internal_out", category="Casa")
        toggle_transaction_giroconto(s, "a")
        assert _state(s, "a")[1] == "Casa"


def test_the_repair_gives_the_fallback_and_leaves_giroconti_alone(engine):
    with get_session(engine) as s:
        _tx(s, "spent", "-30", "expense")
        _tx(s, "card", "-12", "card_tx")
        _tx(s, "earned", "40", "income")
        _tx(s, "giro", "-100", "internal_out")
        _tx(s, "proposed", "-8", "expense")
        s.get(Transaction, "proposed").model_category = "Casa"
        s.get(Transaction, "proposed").model_subcategory = "Affitto"
        s.commit()
    _migrate_empty_category_to_fallback(engine)
    _migrate_empty_category_to_fallback(engine)
    with engine.connect() as conn:
        rows = dict((i, (c, r)) for i, c, r in conn.execute(text(
            'SELECT id, category, to_review FROM "transaction"')).fetchall())
    assert rows == {"spent": ("Altro", 1), "card": ("Altro", 1),
                    "earned": ("Altro entrate", 1), "giro": (None, 0), "proposed": ("Casa", 1)}


def test_the_fallback_income_is_the_generic_subcategory_not_the_first(engine):
    from db.repository import get_fallback_categories
    with get_session(engine) as s:
        assert get_fallback_categories(s) == {"expense": ("Altro", "Spese non classificate"),
                                              "income": ("Altro entrate", "Entrate non classificate")}
