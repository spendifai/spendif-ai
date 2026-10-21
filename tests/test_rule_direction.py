"""Every rule has a direction and holds only for the movements in it
(spec 18; founder 2026-10-21: "le regole non rispettano la direzione...
questo e' l'errore"). The case: two spending movements put in Salute wrote
"starts with FONDAZIONE ISTITUTO", which took the income of another
foundation. Synthetic data only."""
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.categorizer import CategoryRule
from db.models import (
    Base, CategoryRule as RuleRow, TaxonomyCategory, Transaction, _migrate_rule_direction,
)
from db import repository


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'l.db'}")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    s = sessionmaker(bind=engine)()
    s.add_all([TaxonomyCategory(name="Salute", type="expense"),
               TaxonomyCategory(name="Lavoro autonomo", type="income")])
    s.flush()
    yield s
    s.close()


def _tx(s, i, desc, amount, cat=None, src=None):
    s.add(Transaction(id=i, date="2026-08-03", amount=Decimal(amount), description=desc,
                      tx_type="expense" if Decimal(amount) < 0 else "income",
                      category=cat, category_source=src))
    s.flush()
    return s.get(Transaction, i)


def test_a_rule_matches_only_its_direction():
    r = CategoryRule(id=1, pattern="FONDAZIONE ISTITUTO", match_type="starts_with", doc_type=None,
                     category="Salute", subcategory="", direction="out")
    assert r.matches("Fondazione Istituto", None, -30)
    assert not r.matches("Fondazione Istituto Tecnologico Superiore", None, 5788.62)


def test_a_category_rule_takes_the_direction_of_its_category(session):
    rule, _ = repository.create_category_rule(session, "FONDAZIONE ISTITUTO", "starts_with", "Salute")
    assert rule.direction == "out"
    spent = _tx(session, "s", "Fondazione Istituto", "-30")
    earned = _tx(session, "e", "Fondazione Istituto Tecnologico Superiore", "5788.62", "Lavoro autonomo", "llm")
    repository.apply_all_rules_to_all_transactions(session, repository.get_category_rules(session))
    assert spent.category == "Salute"
    assert earned.category == "Lavoro autonomo"


def test_a_giroconto_both_ways_is_two_rules(session):
    _tx(session, "a", "Conto Risparmio", "-500")
    _tx(session, "b", "Conto Risparmio", "200")
    repository.create_category_rule(session, "Conto Risparmio", "exact", "", is_giroconto=True)
    dirs = sorted(r.direction for r in session.query(RuleRow).filter(RuleRow.is_giroconto.is_(True)))
    assert dirs == ["in", "out"]


def test_migration_gives_directions_and_frees_the_wrong_movements(engine, session):
    session.add(RuleRow(pattern="FONDAZIONE ISTITUTO", match_type="starts_with",
                        category="Salute", subcategory="Altro salute"))
    session.add(RuleRow(pattern="Conto Risparmio", match_type="exact", category="",
                        is_giroconto=True))
    _tx(session, "a", "Conto Risparmio", "-500")
    _tx(session, "b", "Conto Risparmio", "200")
    wrong = _tx(session, "e", "Fondazione Istituto Tecnologico Superiore", "5788.62", "Salute", "rule")
    right = _tx(session, "s", "Fondazione Istituto", "-30", "Salute", "rule")
    session.commit()
    _migrate_rule_direction(engine)
    _migrate_rule_direction(engine)  # idempotent
    session.expire_all()
    rules = {(r.pattern, r.direction) for r in session.query(RuleRow)}
    assert rules == {("FONDAZIONE ISTITUTO", "out"), ("Conto Risparmio", "out"), ("Conto Risparmio", "in")}
    assert wrong.category is None and wrong.to_review is True
    assert right.category == "Salute"
