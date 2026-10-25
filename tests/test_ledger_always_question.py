"""After several equal choices by hand on one counterpart, the Registry asks
whether they become a rule ("Sempre cosi'?").

Spec 18 par. 5.2 (founder, 2026-10-25, Pasticceria Clivati): a change of
category in the Registry is a one-off; changing one movement after another of
the same counterpart to the same category is "always like this" said without
anyone asking.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from services.rule_service import ALWAYS_QUESTION_MIN, RuleService


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))


def _add(engine, n, source="manual", category="Alimentari", desc="PASTICCERIA CLIVATI",
         amount="-8", start=0):
    with get_session(engine) as s:
        for i in range(start, start + n):
            s.add(Transaction(
                id=f"t{i}", date=f"2026-09-{1 + i:02d}", amount=Decimal(amount), description=desc,
                source_file="f", account_label="a", tx_type="expense",
                category=category, subcategory="Pasticceria", category_source=source))
        s.commit()
    return [f"t{i}" for i in range(start, start + n)]


def test_the_threshold_is_three():
    assert ALWAYS_QUESTION_MIN == 3


def test_below_the_threshold_nothing_is_asked(engine):
    ids = _add(engine, ALWAYS_QUESTION_MIN - 1)
    assert RuleService(engine).always_questions(ids) == []


def test_at_the_threshold_one_question_per_counterpart_and_category(engine):
    ids = _add(engine, ALWAYS_QUESTION_MIN)
    [q] = RuleService(engine).always_questions(ids)
    assert q["description"].casefold() == "pasticceria clivati"   # as the ledger keeps it
    assert {k: q[k] for k in ("category", "subcategory", "direction", "n")} == {
        "category": "Alimentari", "subcategory": "Pasticceria", "direction": "out",
        "n": ALWAYS_QUESTION_MIN}


def test_earlier_hand_choices_count_with_the_ones_just_saved(engine):
    _add(engine, 2)
    saved = _add(engine, 1, start=2)
    [q] = RuleService(engine).always_questions(saved)
    assert q["n"] == 3


def test_automatic_categories_do_not_count(engine):
    _add(engine, 2)
    saved = _add(engine, 2, source="llm", start=2)
    assert RuleService(engine).always_questions(saved) == []


def test_a_rule_that_already_covers_it_is_not_asked_again(engine):
    ids = _add(engine, 3)
    RuleService(engine).create_rule("PASTICCERIA CLIVATI", "exact", "Alimentari", "Pasticceria",
                                    priority=10)
    assert RuleService(engine).always_questions(ids) == []


def test_after_a_no_it_is_not_asked_again(engine):
    ids = _add(engine, 3)
    svc = RuleService(engine)
    [q] = svc.always_questions(ids)
    svc.decline_always(q)
    assert svc.always_questions(ids) == []
    more = _add(engine, 1, start=3)
    assert svc.always_questions(more) == []


def test_a_no_holds_for_that_direction_only(engine):
    ids = _add(engine, 3)
    svc = RuleService(engine)
    [q] = svc.always_questions(ids)
    svc.decline_always(q)
    refunds = _add(engine, 3, amount="8", start=10)
    [q_in] = svc.always_questions(refunds)
    assert q_in["direction"] == "in"


# ── The question on the real page ────────────────────────────────────────────

def _page(db_path: str) -> None:
    from sqlalchemy import create_engine
    from ui.registry_page import render_registry_page
    render_registry_page(create_engine(f"sqlite:///{db_path}"))


def _ask(tmp_path, engine):
    from datetime import date

    from streamlit.testing.v1 import AppTest

    ids = _add(engine, 3)
    _add(engine, 1, source="llm", category="Ristorazione", start=3)
    app = AppTest.from_function(_page, args=(str(tmp_path / "l.db"),), default_timeout=60)
    app.session_state["_always_pending"] = RuleService(engine).always_questions(ids)
    app.session_state["ledger_from"] = date(2026, 1, 1)
    app.session_state["ledger_to"] = date(2026, 12, 31)
    app.run()
    assert not app.exception, app.exception
    assert any("Sempre" in m.value or "Always" in m.value for m in app.info)
    return app


def test_yes_writes_the_rule_and_applies_it(tmp_path, engine):
    app = _ask(tmp_path, engine)
    next(b for b in app.button if b.key == "ledger_always_yes").click().run()
    assert not app.exception, app.exception
    assert app.session_state["_always_pending"] == []
    with get_session(engine) as s:
        auto = s.get(Transaction, "t3")
        assert (auto.category, auto.category_source) == ("Alimentari", "rule")
        assert s.get(Transaction, "t0").category_source == "manual"


def test_no_is_remembered(tmp_path, engine):
    app = _ask(tmp_path, engine)
    next(b for b in app.button if b.key == "ledger_always_no").click().run()
    assert not app.exception, app.exception
    assert RuleService(engine).always_questions(["t0", "t1", "t2"]) == []
    with get_session(engine) as s:
        assert s.get(Transaction, "t3").category == "Ristorazione"


def test_the_third_equal_choice_saved_in_the_grid_asks(tmp_path, engine):
    from datetime import date

    from streamlit.testing.v1 import AppTest

    from ui.registry_page import COL_CAT_SUB
    from ui.widgets.cat_select import join_cat_sub

    _add(engine, 2)
    _add(engine, 1, source="llm", category="Ristorazione", start=2)
    app = AppTest.from_function(_page, args=(str(tmp_path / "l.db"),), default_timeout=60)
    app.session_state["ledger_from"] = date(2026, 1, 1)
    app.session_state["ledger_to"] = date(2026, 12, 31)
    app.run()
    assert not app.exception, app.exception
    # newest first: t2, the one the model categorized
    app.session_state["ledger_editor"] = {
        "edited_rows": {0: {COL_CAT_SUB: join_cat_sub("Alimentari", "Pasticceria")}},
        "added_rows": [], "deleted_rows": []}
    next(b for b in app.button if b.key == "ledger_save").click().run()
    assert not app.exception, app.exception
    with get_session(engine) as s:
        assert s.get(Transaction, "t2").category_source == "manual"
    [q] = app.session_state["_always_pending"]
    assert (q["category"], q["n"]) == ("Alimentari", 3)
