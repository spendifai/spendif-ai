"""A rule applied from a page never changes a choice by hand or a checked
movement, and the counts that announce the change leave them out.

Spec 18 par. 6 and spec 16 principle 7 (founder, 2026-10-25). The import and
"run all rules" already kept them; the pages went through
TransactionService.apply_rule_category, which checked nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db import repository
from db.models import Transaction, create_tables, get_session
from services.rule_service import RuleService
from services.transaction_service import TransactionService


@pytest.fixture
def engine(tmp_path):
    eng = create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))
    with get_session(eng) as s:
        for tx_id, source, checked in (("model", "llm", False), ("hand", "manual", False),
                                       ("checked", "llm", True)):
            s.add(Transaction(
                id=tx_id, date="2026-09-01", amount=Decimal("-10"), description="BAR SOLE",
                source_file="f", account_label="a", tx_type="expense",
                category="Ristorazione", subcategory="Bar", category_source=source,
                human_validated=checked,
                validated_at=datetime.now(timezone.utc) if checked else None))
        s.commit()
    return eng


def _categories(engine):
    with get_session(engine) as s:
        return {t.id: (t.category, t.category_source) for t in s.query(Transaction)}


def test_a_rule_from_a_page_changes_only_the_automatic_category(engine):
    svc = TransactionService(engine)
    changed = {tx_id: svc.apply_rule_category(tx_id, "Spesa", "Alimentari", origin="rule_apply")
               for tx_id in ("model", "hand", "checked")}
    assert changed == {"model": True, "hand": False, "checked": False}
    assert _categories(engine) == {
        "model": ("Spesa", "rule"),
        "hand": ("Ristorazione", "manual"),
        "checked": ("Ristorazione", "llm"),
    }


def test_the_user_can_still_change_a_checked_movement_by_hand(engine):
    with get_session(engine) as s:
        assert repository.update_transaction_category(s, "checked", "Spesa", "Alimentari")
        s.commit()
    assert _categories(engine)["checked"] == ("Spesa", "manual")


def test_the_count_announced_leaves_them_out(engine):
    svc = TransactionService(engine)
    txs = svc.get_by_rule_pattern("BAR SOLE", "exact", "out")
    assert len(txs) == 3
    assert [t.id for t in txs if svc.rule_may_change(t)] == ["model"]


def test_a_rule_written_by_the_guided_flow_leaves_them_alone(engine):
    from ui.widgets.written_rules import write_and_apply

    n, _note = write_and_apply(RuleService(engine), TransactionService(engine), "BAR SOLE",
                               "BAR SOLE", "exact", "Spesa", "Alimentari", origin="counterparts")
    assert n == 1
    assert _categories(engine)["checked"] == ("Ristorazione", "llm")
