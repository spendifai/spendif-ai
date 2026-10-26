"""The menu at the user's level (spec 19): the first day shows the guided
flow, the expense confirmation, the assistant and the Home, last; the rest is
one click away under "More entries"; the level grows with the data, never
goes down by itself, and the user's own choice wins both ways."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from core.user_level import DATA, MONEY, PAGE_LEVEL, is_visible, level_from_data, menu
from db.models import BudgetTarget, CategoryRule, Transaction, create_tables, get_session
from services.user_level_service import UserLevelService
from ui.sidebar import NAV_PAGES


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'u.db'}"))


def test_every_menu_page_has_a_level():
    assert set(NAV_PAGES) == set(PAGE_LEVEL)


def test_the_first_day_menu_ends_with_the_home():
    shown, others = menu(NAV_PAGES, 1, frozenset())
    assert shown == ["expense_confirm", "chat", "home"]
    assert set(others) == set(NAV_PAGES) - set(shown)


def test_a_level_includes_the_ones_below():
    shown2, _ = menu(NAV_PAGES, 2, frozenset())
    assert {"ledger", "import", "check", "home"} <= set(shown2) and "rules" not in shown2
    shown3, _ = menu(NAV_PAGES, 3, frozenset())
    assert set(shown2) < set(shown3) and "settings" in shown3


def test_the_two_axes_of_level_four_are_independent():
    assert is_visible("analytics", 4, {MONEY}) and not is_visible("bulk_edit", 4, {MONEY})
    assert is_visible("bulk_edit", 4, {DATA}) and not is_visible("analytics", 4, {DATA})
    assert not is_visible("analytics", 3, {MONEY})


def test_a_page_without_a_level_is_shown():
    assert is_visible("a_new_page", 1, frozenset())


@pytest.mark.parametrize("counts, expected", [
    ((0, 0, 0), (1, frozenset())), ((5, 0, 0), (2, frozenset())),
    ((5, 1, 0), (3, frozenset())), ((5, 1, 1), (4, frozenset({MONEY}))),
])
def test_the_level_the_data_show(counts, expected):
    assert level_from_data(*counts) == expected


def _movement(engine):
    with get_session(engine) as s:
        s.add(Transaction(id="t1", date="2026-10-01", amount=Decimal("-5"), description="BAR",
                          source_file="f", account_label="a", tx_type="expense"))
        s.commit()


def test_a_new_install_starts_at_the_first_level(engine):
    assert UserLevelService(engine).level() == (1, frozenset())


def test_the_level_grows_with_the_data_and_never_goes_down(engine):
    svc = UserLevelService(engine)
    _movement(engine)
    with get_session(engine) as s:
        s.add(CategoryRule(pattern="BAR", match_type="exact", category="Vitto", subcategory="Pasti"))
        s.commit()
    assert svc.level() == (3, frozenset())
    with get_session(engine) as s:
        s.query(CategoryRule).delete()
        s.query(Transaction).delete()
        s.commit()
    assert svc.level() == (3, frozenset())          # nothing lowers it by itself


def test_the_users_choice_wins_both_ways(engine):
    svc = UserLevelService(engine)
    _movement(engine)
    svc.set_override(4, {MONEY, DATA})
    assert svc.level() == (4, frozenset({MONEY, DATA}))
    svc.set_override(1)
    assert svc.level() == (1, frozenset())
    svc.set_override(None)
    assert svc.override() is None and svc.level() == (2, frozenset())


def test_a_budget_opens_the_money_axis(engine):
    with get_session(engine) as s:
        s.add(BudgetTarget(category="Svago", target_pct=10))
        s.commit()
    assert UserLevelService(engine).level() == (4, frozenset({MONEY}))


def _sidebar(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    import streamlit as st
    from sqlalchemy import create_engine
    from services.user_level_service import UserLevelService
    from ui.i18n import set_language
    from ui.sidebar import MENU, NAV_PAGES, render_sidebar
    set_language("it")
    st.session_state[MENU] = UserLevelService(create_engine(f"sqlite:///{db_path}")).menu(NAV_PAGES)
    st.session_state.setdefault("page", "home")
    render_sidebar()


def test_the_sidebar_shows_the_level_and_keeps_the_rest_one_click_away(tmp_path):
    db = tmp_path / "s.db"
    create_tables(create_engine(f"sqlite:///{db}"))
    app = AppTest.from_function(_sidebar, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    keys = [b.key for b in app.sidebar.button]
    assert keys[:4] == ["nav_guided", "nav_expense_confirm", "nav_chat", "nav_home"]
    assert "nav_ledger" in keys                      # under "More entries"
    assert len(app.sidebar.expander) == 1
    app.button(key="nav_ledger").click().run()
    assert not app.exception, app.exception
    assert app.session_state["page"] == "ledger"


def _settings(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    from sqlalchemy import create_engine
    from ui.i18n import set_language
    from ui.settings_page import render_settings_page
    set_language("it")
    render_settings_page(create_engine(f"sqlite:///{db_path}"))


def test_the_settings_choice_is_saved_and_back_to_automatic(tmp_path):
    db = tmp_path / "o.db"
    eng = create_tables(create_engine(f"sqlite:///{db}"))
    app = AppTest.from_function(_settings, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    app.selectbox(key="settings_menu_level").set_value(6).run()     # all entries
    assert not app.exception, app.exception
    assert UserLevelService(eng).level() == (4, frozenset({MONEY, DATA}))
    app.selectbox(key="settings_menu_level").set_value(0).run()     # automatic
    assert UserLevelService(eng).override() is None
