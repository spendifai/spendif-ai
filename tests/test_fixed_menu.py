"""The menu is the same for everyone (spec 19, 11bis): the guided flow, the
check, the expense confirmation, the assistant and the Home on top, in this
order; every other page in one and only one of two closed groups, the one
holding the page on screen open; the app lands on the Home, or on the start
page when the registry is empty."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import Transaction, create_tables, get_session
from ui.sidebar import MENU_GROUPS, NAV_PAGES, TOP_PAGES
from ui.start_page import landing_page


def test_every_page_is_on_top_or_in_exactly_one_group():
    grouped = [key for _, keys in MENU_GROUPS for key in keys]
    assert len(grouped) == len(set(grouped))
    assert not set(grouped) & set(TOP_PAGES)
    assert set(grouped) | set(TOP_PAGES) == set(NAV_PAGES)


def test_the_top_of_the_menu_ends_with_the_home():
    assert TOP_PAGES == ["check", "expense_confirm", "chat", "home"]


def _sidebar(page: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    """The sidebar alone, on *page*.

    Args:
        page: the page key on screen when the sidebar is drawn.
    """
    import streamlit as st
    from ui.i18n import set_language
    from ui.sidebar import render_sidebar
    set_language("it")
    st.session_state.setdefault("page", page)
    render_sidebar()


@pytest.mark.parametrize("page, open_group", [("home", None), ("ledger", 0), ("rules", 1)])
def test_the_menu_is_fixed_and_opens_the_group_on_screen(page, open_group):
    app = AppTest.from_function(_sidebar, args=(page,), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    keys = [b.key for b in app.sidebar.button]
    assert keys[:5] == ["nav_guided", "nav_check", "nav_expense_confirm", "nav_chat", "nav_home"]
    labels = [e.label for e in app.sidebar.expander]
    assert labels == ["📊 Approfondimenti", "⚙️ Regole e impostazioni"]
    assert [e.proto.expanded for e in app.sidebar.expander] == [i == open_group for i in range(2)]


def test_a_page_in_a_group_is_one_click_away():
    app = AppTest.from_function(_sidebar, args=("home",), default_timeout=60)
    app.run()
    app.button(key="nav_settings").click().run()
    assert not app.exception, app.exception
    assert app.session_state["page"] == "settings"


@pytest.mark.parametrize("with_movement, expected", [(False, "start"), (True, "home")])
def test_the_app_lands_on_the_home_unless_the_registry_is_empty(tmp_path, with_movement, expected):
    db = tmp_path / "l.db"
    engine = create_tables(create_engine(f"sqlite:///{db}"))
    if with_movement:
        with get_session(engine) as s:
            s.add(Transaction(id="t1", date="2026-10-01", amount=Decimal("-5"), description="BAR",
                              source_file="f", account_label="a", tx_type="expense"))
            s.commit()
    assert landing_page(engine) == expected


def test_the_settings_no_longer_choose_the_menu(tmp_path):
    db = tmp_path / "o.db"
    create_tables(create_engine(f"sqlite:///{db}"))

    def _settings(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
        from sqlalchemy import create_engine
        from ui.i18n import set_language
        from ui.settings_page import render_settings_page
        set_language("it")
        render_settings_page(create_engine(f"sqlite:///{db_path}"))

    app = AppTest.from_function(_settings, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    assert "settings_menu_level" not in [s.key for s in app.selectbox]
