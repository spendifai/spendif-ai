"""The mixed categories step of the guided workflow.

A counterpart with movements in more than one category is proposed until
the user either makes one category a rule or looks at its movements one by
one; after that only movements added by a later import bring it back.
Here "done" means categorized by the user, never checked as spent.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from db.repository import create_category_rule, get_category_rules
from services.guided_flow_service import GuidedFlowService
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """Rules are written without a model here: the merchant list or the exact text."""
    from services.rule_service import RuleService

    def _down(*a, **kw):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(RuleService, "_ask_model_for_keys", _down)


@pytest.fixture(autouse=True)
def _keep_language():
    before = get_language()
    yield
    set_language(before)


def _tx(session, tx_id, description, amount, day, category, *, source="llm"):
    session.add(Transaction(
        id=tx_id, date=day, amount=Decimal(str(amount)), currency="EUR",
        description=description, source_file="f.csv", doc_type="bank_statement",
        account_label="BancaX", tx_type="expense", to_review=False,
        category=category, subcategory="", category_source=source,
    ))


def _seed_mixed(session, *, source="llm"):
    _tx(session, "p1", "PASTICCERIA X", -10, "2026-08-01", "Alimentari", source=source)
    _tx(session, "p2", "PASTICCERIA X", -12, "2026-08-05", "Ristorazione", source=source)
    _tx(session, "c1", "COOP", -50, "2026-08-02", "Alimentari")


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def test_proposes_counterparts_with_more_than_one_category(engine):
    with get_session(engine) as s:
        _seed_mixed(s)
        s.commit()
    mixed = GuidedFlowService(engine).mixed_counterparts()
    assert [m["description"] for m in mixed] == ["Pasticceria X"]
    assert mixed[0]["categories"] == [("Alimentari", 1), ("Ristorazione", 1)]


def test_a_rule_takes_the_counterpart_out(engine):
    with get_session(engine) as s:
        _seed_mixed(s)
        create_category_rule(s, "PASTICCERIA X", "exact", "Ristorazione", "")
        s.commit()
    assert GuidedFlowService(engine).mixed_counterparts() == []


def test_categorized_movements_are_not_asked_again_only_new_ones(engine):
    with get_session(engine) as s:
        _seed_mixed(s, source="manual")
        s.commit()
    svc = GuidedFlowService(engine)
    assert svc.mixed_counterparts() == []
    assert "mixed" in svc.closed_steps()

    with get_session(engine) as s:                     # the next import
        _tx(s, "p3", "Pasticceria X", -9, "2026-08-20", "Alimentari")
        s.commit()
    mixed = svc.mixed_counterparts()
    assert [(m["description"], m["tx_count"], m["uncategorized_count"]) for m in mixed] == [
        ("Pasticceria X", 3, 1),
    ]
    assert [tx.id for tx in svc.uncategorized_movements("pasticceria x")] == ["p3"]


def test_rule_categorized_spending_counts_what_the_rules_spared(engine):
    with get_session(engine) as s:
        _tx(s, "r1", "AFFITTO", -900, "2026-08-01", "Casa", source="rule")
        _tx(s, "r2", "AFFITTO", -900, "2026-07-01", "Casa", source="rule")
        _tx(s, "l1", "COOP", -50, "2026-08-02", "Alimentari")
        s.commit()
    assert GuidedFlowService(engine).rule_categorized_spending() == (2, 1800.0)


# ── The page ─────────────────────────────────────────────────────────────────

def _script(db_path: str) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    from sqlalchemy import create_engine

    from ui.i18n import set_language
    from ui.mixed_page import render_mixed_page

    set_language("en")
    render_mixed_page(create_engine(f"sqlite:///{db_path}"))


@pytest.fixture
def ledger(tmp_path):
    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        _seed_mixed(s)
        s.commit()
    return db_path, eng


def _run(db_path):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_script, kwargs={"db_path": str(db_path)})
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def _button(app, key):
    return next(b for b in app.button if b.key == key)


def test_page_explains_both_ways_out(ledger):
    app = _run(ledger[0])
    text = " ".join(m.value for m in app.markdown)
    assert "If it is a mistake" in text and "one by one" in text
    assert "Pasticceria X" in text
    assert any("1 Alimentari · 1 Ristorazione" in c.value for c in app.caption)


def test_always_this_makes_a_rule_for_every_movement(ledger):
    db_path, eng = ledger
    app = _run(db_path)
    next(sb for sb in app.selectbox if sb.key == "mx_cat_Pasticceria X").set_value("Ristorazione / Bar / caffè")
    _button(app, "mx_always_Pasticceria X").click().run(timeout=30)
    assert not app.exception, app.exception
    with get_session(eng) as s:
        assert [(r.pattern, r.category, r.subcategory) for r in get_category_rules(s)] == [
            ("Pasticceria X", "Ristorazione", "Bar / caffè"),
        ]
        assert {s.get(Transaction, i).category for i in ("p1", "p2")} == {"Ristorazione"}
        assert {s.get(Transaction, i).category_source for i in ("p1", "p2")} == {"rule"}
    assert any("No counterpart" in m.value for m in app.success)


def test_one_by_one_categorizes_the_movements_without_checking_them(ledger):
    db_path, eng = ledger
    app = _run(db_path)
    _button(app, "mx_each_Pasticceria X").click().run(timeout=30)
    assert len(app.dataframe[0].value) == 2
    _button(app, "mx_done_Pasticceria X").click().run(timeout=30)
    assert not app.exception, app.exception
    with get_session(eng) as s:
        assert s.get(Transaction, "p1").category == "Alimentari"   # left as it was
        for i in ("p1", "p2"):
            tx = s.get(Transaction, i)
            assert tx.category_source == "manual"                  # one-off, not a rule
            assert tx.validated_at is None                         # not checked as spent
    assert GuidedFlowService(eng).mixed_counterparts() == []


def test_always_mixed_makes_it_multi_category_and_shows_only_the_movements(ledger):
    """Spec 24: "depends on what was bought", every time. The question is not
    asked again; the movements are there to look at one by one."""
    from services.rule_service import RuleService

    db_path, eng = ledger
    app = _run(db_path)
    _button(app, "mx_multi_Pasticceria X").click().run(timeout=30)
    assert not app.exception, app.exception
    with get_session(eng) as s:
        assert s.get(Transaction, "p1").category_source == "multi"
    keys = {b.key for b in app.button}
    assert "mx_always_Pasticceria X" not in keys and "mx_multi_Pasticceria X" not in keys
    assert "mx_done_Pasticceria X" in keys                  # straight to one by one
    assert RuleService(eng).get_rules()[0].is_multi_category
