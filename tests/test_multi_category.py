"""Multi-category counterparts: the place where everything is bought.

Spec: documents/04_software_engineering/24_controparti_multicategoria.md.
Founder, 2026-10-21: a marketplace has no single category; its movements
take the generic category without the model and are seen one by one.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core import categorizer
from core.categorizer import CategorizationResult, CategoryRule, TaxonomyConfig, categorize_batch
from core.models import CategorySource, Confidence
from db.models import CategoryRule as RuleRow
from db.models import Transaction, create_tables, get_session
from db.repository import create_category_rule
from services.guided_flow_service import GuidedFlowService
from services.rule_service import RuleService
from services.transaction_service import TransactionService

_TAX = TaxonomyConfig(expenses={"Shopping": ["Libri"], "Altro": ["Spese non classificate"],
                                "Abbonamenti": ["Streaming"]},
                      income={"Entrate": ["Rimborsi"]})
_FALLBACK = {"expense": ("Altro", "Spese non classificate"), "income": ("Altro entrate", "Varie")}


@pytest.fixture
def model(monkeypatch):
    asked: list[str] = []

    def _fake(transactions, indices, results, *args, **kwargs):
        for i in indices:
            asked.append(transactions[i]["description"])
            results[i] = CategorizationResult("Shopping", "Libri", Confidence.high, CategorySource.llm)

    monkeypatch.setattr(categorizer, "_run_llm_batch_group", _fake)
    return asked


def _multi(pattern):
    return CategoryRule(id=1, pattern=pattern, match_type="exact", category="", subcategory="",
                        doc_type=None, is_multi_category=True)


def _tx(desc, amount=-20):
    return {"id": desc + str(amount), "description": desc, "amount": amount,
            "raw_description": desc, "doc_type": "credit_card"}


# ── Import ───────────────────────────────────────────────────────────────────

def test_its_movements_take_the_generic_category_without_the_model(model):
    [r] = categorize_batch([_tx("Amazon Marketplace")], _TAX, [_multi("AMAZON MARKETPLACE")],
                           llm_backend=object(), fallback_categories=_FALLBACK)
    assert (r.category, r.subcategory, r.source, r.to_review) == (
        "Altro", "Spese non classificate", CategorySource.multi, True)
    assert model == []


def test_it_wins_over_a_category_rule_and_leaves_other_counterparts_alone(model):
    starts = CategoryRule(id=2, pattern="AMAZON", match_type="starts_with", category="Shopping",
                          subcategory="Libri", doc_type=None, priority=10)
    prime = CategoryRule(id=3, pattern="AMAZON PRIME", match_type="exact", category="Abbonamenti",
                         subcategory="Streaming", doc_type=None)
    marketplace, prime_tx = categorize_batch(
        [_tx("AMAZON MARKETPLACE"), _tx("AMAZON PRIME")], _TAX,
        [starts, prime, _multi("AMAZON MARKETPLACE")], llm_backend=object(),
        fallback_categories=_FALLBACK)
    assert marketplace.source == CategorySource.multi
    assert prime_tx.category in ("Shopping", "Abbonamenti") and prime_tx.source == CategorySource.rule


# ── Ledger and workflow ──────────────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))


def _saved(s, tx_id, desc, amount, day, source="llm", category="Shopping"):
    s.add(Transaction(id=tx_id, date=day, amount=Decimal(str(amount)), description=desc,
                      source_file="f", account_label="a", tx_type="expense",
                      category=category, subcategory="", category_source=source))


def test_marking_it_moves_the_saved_movements_and_keeps_hand_choices(engine):
    with get_session(engine) as s:
        _saved(s, "a", "AMAZON MARKETPLACE", -30, "2026-08-01")
        _saved(s, "b", "Amazon Marketplace", -12, "2026-08-02", source="manual", category="Libri")
        s.commit()
    assert RuleService(engine).set_multi_category("AMAZON MARKETPLACE") == 1
    with get_session(engine) as s:
        a, b = s.get(Transaction, "a"), s.get(Transaction, "b")
        assert (a.category, a.category_source, a.to_review) == ("Altro", "multi", True)
        assert (b.category, b.category_source) == ("Libri", "manual")
        [rule] = s.query(RuleRow).all()
        assert (rule.match_type, rule.category, rule.is_multi_category) == ("exact", "", True)


def test_the_workflow_does_not_ask_it_again_and_shows_its_movements_one_by_one(engine):
    with get_session(engine) as s:
        _saved(s, "a", "Amazon Marketplace", -30, "2026-08-01")
        _saved(s, "b", "Amazon Marketplace", -40, "2026-08-02")
        _saved(s, "c", "Affitto", -900, "2026-08-03")
        s.commit()
    RuleService(engine).set_multi_category("Amazon Marketplace")
    flow = GuidedFlowService(engine)
    asked = [x["description"] for x in flow.expense_counterparts() if not x["categorized_by_user"]]
    assert asked == ["Affitto"]
    [mixed] = flow.mixed_counterparts()
    assert (mixed["description"], mixed["is_multi_category"], mixed["to_see_one_by_one"]) == (
        "Amazon Marketplace", True, 2)
    # A choice there is a one-off made by hand, never a rule.
    TransactionService(engine).update_category("a", "Shopping", "Libri", origin="mixed")
    with get_session(engine) as s:
        assert s.get(Transaction, "a").category_source == "manual"
        assert s.query(RuleRow).count() == 1
    [mixed] = flow.mixed_counterparts()
    assert mixed["to_see_one_by_one"] == 1


def test_unmarking_brings_the_counterpart_back_to_decide(engine):
    with get_session(engine) as s:
        _saved(s, "a", "Amazon Marketplace", -30, "2026-08-01")
        s.commit()
    svc = RuleService(engine)
    svc.set_multi_category("Amazon Marketplace")
    svc.set_multi_category("Amazon Marketplace", False)
    with get_session(engine) as s:
        assert s.query(RuleRow).count() == 0
    asked = [x["description"] for x in GuidedFlowService(engine).expense_counterparts()
             if not x["categorized_by_user"]]
    assert asked == ["Amazon Marketplace"]


def test_the_registry_filter_lists_the_movements_to_see(engine):
    with get_session(engine) as s:
        _saved(s, "a", "AMAZON MARKETPLACE", -30, "2026-08-01")
        _saved(s, "b", "AFFITTO", -900, "2026-08-03")
        s.commit()
    RuleService(engine).set_multi_category("AMAZON MARKETPLACE")
    txs = TransactionService(engine).get_transactions(filters={"category_source": "multi"})
    assert [t.id for t in txs] == ["a"]


# ── A reminder, not a rule that runs (spec 24 par. 3, founder 2026-10-25) ────

def test_the_rule_is_not_run_but_shown(engine):
    with get_session(engine) as s:
        _saved(s, "a", "AMAZON MARKETPLACE", -30, "2026-08-01")
        s.commit()
    svc = RuleService(engine)
    svc.set_multi_category("AMAZON MARKETPLACE")
    with get_session(engine) as s:
        [rule] = s.query(RuleRow).all()
        assert (rule.not_show, rule.not_run) == (False, True)
    assert [r.pattern for r in svc.get_rules()] == ["AMAZON MARKETPLACE"]


def test_reapplying_the_rules_never_runs_it(engine):
    """Once decided, the rule is not run again: a movement of the counterpart
    that has another category (here from an older rule) is left as it is, and
    a broader rule with a category does not take it either."""
    svc = RuleService(engine)
    svc.set_multi_category("AMAZON MARKETPLACE")
    with get_session(engine) as s:
        _saved(s, "late", "AMAZON MARKETPLACE", -15, "2026-08-03", source="rule",
               category="Abbonamenti")
        s.commit()
    svc.create_rule("AMAZON", "starts_with", "Shopping", "Libri", priority=10)
    svc.apply_to_all()
    with get_session(engine) as s:
        late = s.get(Transaction, "late")
        assert (late.category, late.category_source) == ("Abbonamenti", "rule")


def test_the_import_still_reads_it(model):
    rule = _multi("AMAZON MARKETPLACE")
    rule.not_run = True
    [r] = categorize_batch([_tx("Amazon Marketplace")], _TAX, [rule],
                           llm_backend=object(), fallback_categories=_FALLBACK)
    assert (r.source, r.to_review) == (CategorySource.multi, True)
    assert model == []


def test_writing_a_category_rule_on_it_makes_it_run(engine):
    svc = RuleService(engine)
    svc.set_multi_category("AMAZON MARKETPLACE")
    svc.create_rule("AMAZON MARKETPLACE", "exact", "Shopping", "Libri", priority=10)
    with get_session(engine) as s:
        rules = [(r.is_multi_category, r.not_run) for r in s.query(RuleRow)]
    assert (False, False) in rules and (True, True) not in rules
