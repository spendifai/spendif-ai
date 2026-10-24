"""A giroconto has no category, whatever path turns a movement into one.

Before 2026-10-21 a movement that became a giroconto after the import (by
hand, by a giroconto rule, by a transfer pair) kept the category the import
had given it, and the giroconti filled "Altro". Synthetic data only.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.categorizer import CategoryRule
from db.models import Base, Transaction, _migrate_giroconti_without_category
from db.repository import (
    apply_all_rules_to_all_transactions,
    apply_rules_to_transactions,
    bulk_set_giroconto_by_description,
    toggle_transaction_giroconto,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _tx(session, tx_id="t1", description="Mah Deposito Risparmi", amount="-5", **kw):
    fields = dict(
        id=tx_id, date="2026-10-01", amount=Decimal(amount), description=description,
        tx_type="expense", category="Altro", subcategory="Spese non classificate",
        category_source="llm", category_confidence="low",
        model_category="Altro", model_subcategory="Spese non classificate", model_confidence="low",
    )
    fields.update(kw)
    tx = Transaction(**fields)
    session.add(tx)
    session.flush()
    return tx


def test_building_a_giroconto_keeps_what_it_is_given(session):
    tx = _tx(session, tx_type="internal_out", category=None, subcategory=None, category_source=None)
    assert tx.category is None and tx.tx_type == "internal_out"


def test_becoming_a_giroconto_empties_the_category(session):
    tx = _tx(session)
    toggle_transaction_giroconto(session, tx.id)
    assert tx.tx_type == "internal_out"
    assert (tx.category, tx.subcategory, tx.category_source, tx.category_confidence) == (None,) * 4
    assert tx.model_category == "Altro"  # the product's proposal stays


def test_a_category_chosen_by_hand_goes_too(session):
    tx = _tx(session, category="Casa", subcategory="Affitto", category_source="manual")
    bulk_set_giroconto_by_description(session, "Mah Deposito Risparmi", True)
    assert tx.category is None


def test_leaving_the_giroconto_gives_back_the_proposal_to_review(session):
    tx = _tx(session, model_category="Casa", model_subcategory="Affitto", model_confidence="medium")
    toggle_transaction_giroconto(session, tx.id)
    toggle_transaction_giroconto(session, tx.id)
    assert tx.tx_type == "expense"
    assert (tx.category, tx.subcategory, tx.category_confidence) == ("Casa", "Affitto", "medium")
    assert tx.to_review is True


def test_leaving_the_giroconto_without_a_proposal_takes_the_fallback(session):
    """Never without a category (founder, 2026-10-23): the fallback of the
    direction, to review."""
    tx = _tx(session, tx_type="internal_out", category=None, subcategory=None,
             category_source=None, model_category=None, model_subcategory=None)
    toggle_transaction_giroconto(session, tx.id)
    assert tx.tx_type == "expense" and tx.to_review is True
    assert (tx.category, tx.subcategory) == ("Altro", "Spese non classificate")


def test_a_category_rule_does_not_categorize_a_giroconto(session):
    tx = _tx(session, tx_type="internal_out", category=None, subcategory=None, category_source=None)
    rule = CategoryRule(id=1, pattern="Mah Deposito Risparmi", match_type="exact", doc_type=None,
                        category="Casa", subcategory="Affitto")
    assert apply_rules_to_transactions(session, [tx.id], [rule]) == 0
    assert apply_all_rules_to_all_transactions(session, [rule]) == (0, 0)
    assert tx.category is None


def test_migration_empties_the_giroconti_that_kept_a_category():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        Transaction(id="g", date="2026-10-01", amount=Decimal("-5"), tx_type="internal_out",
                    category="Altro", subcategory="Spese non classificate", category_source="llm",
                    model_category="Altro"),
        Transaction(id="e", date="2026-10-01", amount=Decimal("-5"), tx_type="expense",
                    category="Casa", subcategory="Affitto", category_source="rule"),
    ])
    s.commit()
    _migrate_giroconti_without_category(engine)
    _migrate_giroconti_without_category(engine)  # idempotent
    s.expire_all()
    assert s.get(Transaction, "g").category is None
    assert s.get(Transaction, "g").model_category == "Altro"
    assert s.get(Transaction, "e").category == "Casa"
    s.close()
