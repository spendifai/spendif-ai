"""The check confirms the category of its movement (spec 16, principle 7);
the fallback category is no example, and the model's answer rewrites the
automatic past of its counterpart (spec 22, 4 and 5bis). Synthetic data."""
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.categorizer import CategoryRule
from db.models import Base, TaxonomyCategory, TaxonomySubcategory, Transaction
from db import repository


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    altro = TaxonomyCategory(name="Altro", type="expense", is_fallback=True)
    s.add(altro)
    s.flush()
    s.add(TaxonomySubcategory(category_id=altro.id, name="Spese non classificate"))
    s.flush()
    yield s
    s.close()


def _tx(s, i, desc, cat, src="llm", amount="-5", validated=False, tx_type="expense"):
    s.add(Transaction(id=i, date="2026-10-01", amount=Decimal(amount), description=desc,
                      tx_type=tx_type, category=cat, subcategory="", category_source=src,
                      human_validated=validated))
    s.flush()
    return s.get(Transaction, i)


def test_a_rule_does_not_change_a_checked_movement(session):
    checked = _tx(session, "c", "Bar Sport", "Casa", validated=True)
    other = _tx(session, "o", "Bar Sport", "Casa")
    rule = CategoryRule(id=1, pattern="Bar Sport", match_type="exact", doc_type=None,
                        category="Ristorazione", subcategory="")
    repository.apply_rules_to_transactions(session, ["c", "o"], [rule])
    repository.apply_all_rules_to_all_transactions(session, [rule])
    assert checked.category == "Casa" and other.category == "Ristorazione"


def test_the_fallback_is_no_example(session):
    _tx(session, "a1", "Fgm Srl", "Altro")
    _tx(session, "a2", "Fgm Srl", "Altro")
    _tx(session, "b1", "Bar Sport", "Altro")
    _tx(session, "b2", "Bar Sport", "Ristorazione")
    known = repository.known_counterpart_categories(session)
    assert ("fgm srl", "out") not in known
    assert known[("bar sport", "out")] == ("Ristorazione", "")


def test_checked_movements_decide_the_counterpart_category(session):
    _tx(session, "m1", "Coop", "Casa")
    _tx(session, "m2", "Coop", "Casa")
    _tx(session, "c1", "Coop", "Alimentari", validated=True)
    assert repository.known_counterpart_categories(session)[("coop", "out")] == ("Alimentari", "")


def test_the_model_answer_rewrites_the_automatic_past_only(session):
    old_altro = _tx(session, "p1", "Fgm Srl", "Altro")
    old_history = _tx(session, "p2", "Fgm Srl", "Casa", src="history")
    by_rule = _tx(session, "p3", "Fgm Srl", "Casa", src="rule")
    by_hand = _tx(session, "p4", "Fgm Srl", "Casa", src="manual")
    checked = _tx(session, "p5", "Fgm Srl", "Altro", validated=True)
    refund = _tx(session, "p6", "Fgm Srl", "Altro", amount="5")
    new = [{"id": "n1", "description": "FGM SRL", "amount": -7, "tx_type": "expense",
            "category": "Ristorazione", "subcategory": "Bar", "category_source": "llm"}]
    assert repository.rewrite_with_model_answers(session, new) == 2
    assert (old_altro.category, old_history.category) == ("Ristorazione", "Ristorazione")
    assert by_rule.category == by_hand.category == "Casa"
    assert checked.category == "Altro" and refund.category == "Altro"


def test_an_answer_in_the_fallback_rewrites_nothing(session):
    old = _tx(session, "p1", "Fgm Srl", "Casa")
    new = [{"id": "n1", "description": "Fgm Srl", "amount": -7, "tx_type": "expense",
            "category": "Altro", "subcategory": "Spese non classificate", "category_source": "llm"}]
    assert repository.rewrite_with_model_answers(session, new) == 0
    assert old.category == "Casa"
