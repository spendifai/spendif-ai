"""The rename question of the Registry (spec 27): renaming all, with the
category rule as a choice. Driven through the real page. Synthetic data."""
from decimal import Decimal

from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import Base, Transaction, get_session
from db.repository import get_category_rules
from services.rule_service import RuleService

_RAW = "Bonifico eseguito Rapporto 06 77 157302 rata del mese corrente {n}"


def _ledger(tmp_path):
    db = tmp_path / "ledger.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    with get_session(eng) as s:
        for i in range(3):
            s.add(Transaction(id=f"r{i}", date=f"2026-0{i + 1}-08", amount=Decimal("-921.40"),
                              raw_description=_RAW.format(n=1000 + i), description="Mutuo Casa",
                              tx_type="expense", category="Casa", subcategory="Mutuo",
                              category_source="llm"))
        s.commit()
    return db, eng


def _page(db_path: str) -> None:
    from sqlalchemy import create_engine
    from ui.registry_page import render_registry_page
    render_registry_page(create_engine(f"sqlite:///{db_path}"))


def _period(app) -> None:
    """The months of the synthetic movements, not the current one."""
    from datetime import date
    app.session_state["ledger_from"] = date(2026, 1, 1)
    app.session_state["ledger_to"] = date(2026, 12, 31)


def test_movement_summary(tmp_path):
    _db, eng = _ledger(tmp_path)
    svc = RuleService(eng)
    assert svc.movement_summary("r0") == {"category": "Casa", "subcategory": "Mutuo", "giroconto": False}
    assert svc.movement_summary("missing")["category"] == ""


def test_rename_all_with_the_category_rule(tmp_path):
    db, eng = _ledger(tmp_path)
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.session_state["_own_name_pending"] = [
        ("r0", "Mutuo Casa", 2, {"n": 0, "low": None, "high": None})]
    _period(app)
    app.run()
    assert not app.exception, app.exception
    assert any(c.key == "own_name_rule_r0" and c.value for c in app.checkbox)
    next(b for b in app.button if b.key == "own_name_all_r0").click().run()
    assert not app.exception, app.exception
    with get_session(eng) as s:
        assert any(r.pattern == "Mutuo Casa" and r.category == "Casa" for r in get_category_rules(s))
        assert {s.get(Transaction, f"r{i}").category_source for i in (1, 2)} == {"rule"}


def test_variable_installment_offers_the_same_amount_choice(tmp_path):
    db, eng = _ledger(tmp_path)
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.session_state["_own_name_pending"] = [
        ("r0", "Mutuo Casa", 0, {"n": 2, "low": 900.0, "high": 950.0})]
    _period(app)
    app.run()
    assert not app.exception, app.exception
    assert any(b.key == "own_name_same_r0" for b in app.button)
    next(b for b in app.button if b.key == "own_name_one_r0").click().run()
    assert not app.exception, app.exception
    assert app.session_state["_own_name_pending"] == []
