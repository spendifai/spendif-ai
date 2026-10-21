"""One way to write a counterpart, one way to compare it, everywhere.

Spec 21: the counterpart is tidied (special characters to a space, initials
joined) and written with capital initials ("Google Cloud"); every comparison ignores case,
accents, punctuation and initials written apart (founder, 2026-10-21).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.history_engine import HistoryCache
from db.models import Transaction, create_tables, get_session, same_counterpart
from db.repository import get_counterpart_stats


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))


def _tx(s, tx_id, desc, source="rule", category="Spesa"):
    s.add(Transaction(id=tx_id, date="2026-08-01", amount=Decimal("-5"), description=desc,
                      source_file="f", account_label="a", tx_type="expense",
                      category=category, subcategory="", category_source=source))


@pytest.mark.parametrize("written, stored", [
    ("Clivati 1969 S.R.L.", "Clivati 1969 Srl"),     # capital initials, tidied
    ("  GOOGLE*CLOUD  ", "Google Cloud"),
    ("***", None),
    (None, None),
])
def test_every_write_goes_through_one_rule(written, stored):
    tx = Transaction(description=written)
    assert tx.description == stored
    tx.description = written                          # an assignment too
    assert tx.description == stored


def test_queries_and_groups_ignore_how_it_is_written(engine):
    with get_session(engine) as s:
        _tx(s, "a", "Clivati 1969 SRL")
        _tx(s, "b", "CLIVATI 1969 S.R.L.")
        _tx(s, "c", "clivati 1969 s r l")
        s.commit()
        assert s.query(Transaction).filter(
            same_counterpart(Transaction.description, "CLIVATI 1969 SRL")).count() == 3
        [stat] = get_counterpart_stats(s)
        assert stat["tx_count"] == 3


def test_the_history_finds_a_counterpart_written_another_way(engine):
    with get_session(engine) as s:
        for i in range(5):
            _tx(s, f"t{i}", "ESSELUNGA SPA")
        s.commit()
        cat, _, conf = HistoryCache(s).lookup("Esselunga S.p.A.")
    assert cat == "Spesa" and conf > 0


@pytest.mark.parametrize("text, shown", [
    ("GOOGLE CLOUD", "Google Cloud"),
    ("antica pizzeria LEONE", "Antica Pizzeria Leone"),
    ("3cpayment Radisson", "3cpayment Radisson"),       # a digit first: letters lower
    ("ステーキ", "ステーキ"),                               # no case: unchanged
])
def test_capital_initials(text, shown):
    from core.normalizer import counterpart_case
    assert counterpart_case(text) == shown


def test_saved_upper_case_counterparts_are_rewritten_once(tmp_path):
    from sqlalchemy import text

    from db.models import _migrate_counterparts_written_one_way
    eng = create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))
    with eng.connect() as conn:
        conn.execute(text(
            'INSERT INTO "transaction" (id, date, amount, description, tx_type)'
            " VALUES ('a', '2026-08-01', -5, 'ESSELUNGA CORSICO', 'expense')"))
        conn.commit()
    _migrate_counterparts_written_one_way(eng)
    _migrate_counterparts_written_one_way(eng)          # idempotent
    with get_session(eng) as s:
        assert s.get(Transaction, "a").description == "Esselunga Corsico"
