"""The counterparts step of the guided workflow.

Progress is counted in money over the spending of the landing period; the
tick "always like this" turns the category shown into a rule for every
movement of the counterpart.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.guided_flow import check_progress
from db.models import Transaction, create_tables, get_session
from db.repository import get_category_rules, get_counterpart_stats
from services.guided_flow_service import GuidedFlowService
from services.settings_service import SettingsService
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _keep_language():
    """The page runs in English here: leave the language as it was found."""
    before = get_language()
    yield
    set_language(before)


def _tx(session, tx_id, description, amount, day, *, tx_type="expense",
        category="Alimentari", validated=False):
    from datetime import UTC, datetime
    session.add(Transaction(
        id=tx_id, date=day, amount=Decimal(str(amount)), currency="EUR",
        description=description, source_file="f.csv", doc_type="bank_statement",
        account_label="BancaX", tx_type=tx_type, to_review=False,
        category=category, subcategory="", category_source="llm",
        validated_at=datetime.now(UTC) if validated else None,
    ))


# ── Progress, pure ───────────────────────────────────────────────────────────

def test_progress_on_no_spending_is_reached():
    p = check_progress([])
    assert (p.total, p.missing, p.needed, p.reached) == (0, 0, 0, True)


def test_progress_counts_the_largest_unchecked_first():
    p = check_progress([(500, False), (300, False), (150, True), (50, False)])
    assert p.total == 1000
    assert p.checked == 150
    assert p.missing == pytest.approx(650)
    assert p.needed == 2            # 150 + 500 + 300 reaches 800
    assert not p.reached


def test_progress_reached_at_the_goal():
    p = check_progress([(800, True), (200, False)])
    assert p.reached
    assert (p.missing, p.needed) == (0, 0)


# ── Period figures ───────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def test_stats_in_a_period_keep_checked_from_any_time(engine):
    with get_session(engine) as s:
        _tx(s, "old", "COOP", -20, "2026-01-10", validated=True)   # confirmed long ago
        _tx(s, "a", "COOP", -30, "2026-08-02")
        _tx(s, "b", "COOP", -50, "2026-08-20")
        _tx(s, "c", "BAR", -5, "2026-01-11")                      # outside the period
        s.commit()
        stats = get_counterpart_stats(s, date_from="2026-06-01", date_to="2026-08-31")
    assert [(x["description"], x["tx_count"], x["total_amount"], x["human_checked"])
            for x in stats] == [("COOP", 2, 80.0, True)]


def test_step_uses_spending_of_the_landing_period_largest_first(engine):
    with get_session(engine) as s:
        _tx(s, "a", "AFFITTO", -900, "2026-08-01", category="Casa")
        _tx(s, "b", "COOP", -60, "2026-08-03")
        _tx(s, "c", "COOP", -40, "2026-07-03")
        _tx(s, "d", "STIPENDIO", 2500, "2026-08-27", tx_type="income", category="Reddito")
        _tx(s, "e", "VECCHIO", -10, "2026-01-03")
        s.commit()
    svc = GuidedFlowService(engine)
    assert [x["description"] for x in svc.expense_counterparts()] == ["AFFITTO", "COOP"]
    p = svc.check_progress()
    assert (p.total, p.checked, p.needed) == (1000, 0, 1)


def test_step_closes_when_the_goal_is_reached(engine):
    SettingsService(engine).create_account("BancaX", "")
    with get_session(engine) as s:
        _tx(s, "a", "AFFITTO", -900, "2026-08-01", validated=True)
        _tx(s, "b", "COOP", -100, "2026-08-03")
        s.commit()
    assert "counterparts" in GuidedFlowService(engine).closed_steps()


# ── The page inside the workflow ─────────────────────────────────────────────

def _guided_script(db_path: str) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    import streamlit as st
    from sqlalchemy import create_engine

    from ui.counterparts_page import render_counterparts_page
    from ui.i18n import set_language

    set_language("en")
    st.session_state["page"] = "guided"
    render_counterparts_page(create_engine(f"sqlite:///{db_path}"))


@pytest.fixture
def ledger(tmp_path):
    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        _tx(s, "a1", "AFFITTO", -450, "2026-08-01", category="Casa")
        _tx(s, "a2", "AFFITTO", -450, "2026-07-01", category="Casa")
        _tx(s, "b", "COOP", -100, "2026-08-03", validated=True)
        s.commit()
    return db_path, eng


def _run(db_path):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_guided_script, kwargs={"db_path": str(db_path)})
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def test_guided_view_explains_the_criterion_and_what_is_missing(ledger):
    app = _run(ledger[0])
    text = " ".join(m.value for m in app.markdown)
    assert "Once per counterpart is enough" in text
    assert "8 euros out of 10" in text
    info = " ".join(i.value for i in app.info)
    assert "100,00 €" in info and "1.000,00 €" in info     # checked, total
    assert "700,00 €" in info                              # missing to 800
    assert "the first counterpart in the list" in info
    assert any("move on whenever you want" in c.value for c in app.caption)
    assert list(app.dataframe[0].value.iloc[:, 0]) == ["AFFITTO"]   # checked ones hidden


def test_ticking_always_makes_the_category_a_rule_for_every_movement(ledger):
    from ui.i18n import t

    db_path, eng = ledger
    set_language("en")
    app = _run(db_path)
    edit = {"edited_rows": {0: {t("counterparts.col_checked"): True}},
            "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    save = [b for b in app.button if b.key == "cp_save"]
    assert save, "the tick was not seen as a change"
    app.session_state["cp_editor"] = edit
    save[0].click().run(timeout=30)
    assert not app.exception, app.exception

    with get_session(eng) as s:
        rules = [(r.pattern, r.match_type, r.category) for r in get_category_rules(s)]
        assert rules == [("AFFITTO", "exact", "Casa")]
        for tx_id in ("a1", "a2"):
            tx = s.get(Transaction, tx_id)
            assert tx.category == "Casa"
            assert tx.validated_at is not None
    assert GuidedFlowService(eng).check_progress().reached
