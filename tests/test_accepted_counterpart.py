"""A counterpart ticked with the category it already had is accepted, not ruled.

Founder, 2026-10-24 (spec 16, 3.4): the movements keep their category and the
source that gave it, the counterpart is remembered as done with an exact rule
marked not_show_run, never run and never listed, and writing the same rule
again makes it a normal one.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db import repository
from db.models import CategoryRule, Transaction, create_tables, get_session
from services.rule_service import RuleService


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))


def _tx(s, tx_id, desc, category, source, amount="-10"):
    s.add(Transaction(id=tx_id, date="2026-09-01", amount=Decimal(amount), description=desc,
                      source_file="f", account_label="a", tx_type="expense",
                      category=category, subcategory="", category_source=source,
                      model_category=category, model_subcategory="", model_source=source))


def _seed(engine):
    with get_session(engine) as s:
        _tx(s, "a", "PANIFICIO ROSSI", "Spesa", "nsi")
        _tx(s, "b", "PANIFICIO ROSSI", "Spesa", "llm")
        s.commit()


def test_accepting_keeps_category_and_source(engine):
    _seed(engine)
    RuleService(engine).accept_counterpart("PANIFICIO ROSSI", "Spesa", "")
    with get_session(engine) as s:
        got = {t.id: (t.category, t.category_source) for t in s.query(Transaction)}
        assert got == {"a": ("Spesa", "nsi"), "b": ("Spesa", "llm")}
        assert s.query(CategoryRule).one().not_show_run is True


def test_an_accepted_rule_is_neither_listed_nor_run(engine):
    _seed(engine)
    svc = RuleService(engine)
    svc.accept_counterpart("PANIFICIO ROSSI", "Spesa", "")
    assert svc.get_rules() == []
    with get_session(engine) as s:
        _tx(s, "c", "PANIFICIO ROSSI", "Ristorazione", "llm")
        s.commit()
    svc.apply_to_all()
    with get_session(engine) as s:
        c = s.get(Transaction, "c")
        assert (c.category, c.category_source) == ("Ristorazione", "llm")


def test_an_accepted_counterpart_is_done_for_the_guided_flow(engine):
    _seed(engine)
    with get_session(engine) as s:
        before = {r["description"]: r["categorized_by_user"]
                  for r in repository.get_counterpart_stats(s, negative_only=True)}
    RuleService(engine).accept_counterpart("PANIFICIO ROSSI", "Spesa", "")
    with get_session(engine) as s:
        after = {r["description"]: r["categorized_by_user"]
                 for r in repository.get_counterpart_stats(s, negative_only=True)}
    assert list(before.values()) == [False]
    assert list(after.values()) == [True]


def test_writing_the_same_rule_makes_it_a_real_one(engine):
    _seed(engine)
    svc = RuleService(engine)
    svc.accept_counterpart("PANIFICIO ROSSI", "Spesa", "")
    svc.create_rule("PANIFICIO ROSSI", "exact", "Spesa", "", priority=10)
    with get_session(engine) as s:
        rule = s.query(CategoryRule).one()
        assert rule.not_show_run is False
    assert len(svc.get_rules()) == 1


def test_accepting_never_hides_a_rule_the_user_has(engine):
    _seed(engine)
    svc = RuleService(engine)
    svc.create_rule("PANIFICIO ROSSI", "exact", "Spesa", "", priority=10)
    svc.accept_counterpart("PANIFICIO ROSSI", "Spesa", "")
    with get_session(engine) as s:
        assert s.query(CategoryRule).one().not_show_run is False


def test_the_model_answer_leaves_an_accepted_counterpart_alone(engine):
    _seed(engine)
    RuleService(engine).accept_counterpart("PANIFICIO ROSSI", "Spesa", "")
    with get_session(engine) as s:
        n = repository.rewrite_counterpart_category(s, "PANIFICIO ROSSI", True, "Ristorazione", "")
        assert n == 0
        assert {t.category for t in s.query(Transaction)} == {"Spesa"}


def test_a_starts_with_key_generalizes_only_over_other_counterparts(engine):
    _seed(engine)
    with get_session(engine) as s:
        _tx(s, "d", "PANIFICIO ROSSI MILANO", "Spesa", "llm")
        s.commit()
    svc = RuleService(engine)
    assert svc.generalizes("PANIFICIO ROSSI", "PANIFICIO ROSSI", "starts_with") is True
    assert svc.generalizes("PANIFICIO ROSSI MILANO", "PANIFICIO ROSSI MILANO", "starts_with") is False
    assert svc.generalizes("PANIFICIO ROSSI", "PANIFICIO ROSSI", "exact") is False


def test_the_migration_is_idempotent(tmp_path):
    from db.models import _migrate_add_rule_not_show_run

    engine = create_tables(create_engine(f"sqlite:///{tmp_path / 'o.db'}"))
    _migrate_add_rule_not_show_run(engine)
    _migrate_add_rule_not_show_run(engine)
