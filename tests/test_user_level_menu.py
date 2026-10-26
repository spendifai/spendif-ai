"""The user's level (spec 19): it grows with the data, never goes down by
itself, and the user's own choice wins both ways. Since spec 19, 11bis the
level no longer shapes the menu (tests/test_fixed_menu.py); it stays for the
suspended phases that may give it a visible effect again."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine

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
