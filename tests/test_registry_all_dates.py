""""All dates" in the Registry spans the whole registry (spec 21, 7bis):
the two fields show its first and last date. Taking the dates away only gave
the current month back, and a search for an expense of another month found
nothing."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import Transaction, create_tables, get_session
from services.transaction_service import TransactionService


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "r.db"
    create_tables(create_engine(f"sqlite:///{path}"))
    return path


def _add(db, tx_id, day, amount, desc):
    with get_session(create_engine(f"sqlite:///{db}")) as s:
        s.add(Transaction(id=tx_id, date=day, amount=Decimal(amount), description=desc, source_file="f",
                          account_label="Conto", tx_type="expense", category="Altro", subcategory=""))
        s.commit()


def _page(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    from sqlalchemy import create_engine
    from ui.i18n import set_language
    from ui.registry_page import render_registry_page
    set_language("it")
    render_registry_page(create_engine(f"sqlite:///{db_path}"))


def test_the_span_is_the_first_and_the_last_date(db):
    assert TransactionService(create_engine(f"sqlite:///{db}")).get_date_span() is None
    _add(db, "old", "2024-03-05", "-422.67", "NEGOZIO")
    _add(db, "new", "2026-10-20", "-5", "BAR")
    assert TransactionService(create_engine(f"sqlite:///{db}")).get_date_span() == (
        date(2024, 3, 5), date(2026, 10, 20))


def test_all_dates_then_an_amount_finds_an_old_expense(db):
    _add(db, "old", "2024-03-05", "-422.67", "NEGOZIO")
    _add(db, "new", "2026-10-20", "-5", "BAR")
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    app.button(key="preset_all").click().run()
    assert not app.exception, app.exception
    assert (app.session_state["ledger_from"], app.session_state["ledger_to"]) == (
        date(2024, 3, 5), date(2026, 10, 20))
    app.text_input(key="ledger_desc").input("422,67").run()
    assert not app.exception, app.exception
    assert app.metric[0].value == "1"


def _analysis(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    from sqlalchemy import create_engine
    from ui.analysis_page import render_analysis_page
    from ui.i18n import set_language
    set_language("it")
    render_analysis_page(create_engine(f"sqlite:///{db_path}"))


def _report(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    from sqlalchemy import create_engine
    from ui.i18n import set_language
    from ui.report_page import render_report_page
    set_language("it")
    render_report_page(create_engine(f"sqlite:///{db_path}"))


@pytest.mark.parametrize("page, button, keys", [
    (_analysis, "an_preset_all", ("analytics_from", "analytics_to")),
    (_report, "rpt_preset_all", ("report_from", "report_to")),
])
def test_all_dates_spans_the_registry_in_analysis_and_report(db, page, button, keys):
    _add(db, "old", "2024-03-05", "-422.67", "NEGOZIO")
    _add(db, "new", "2026-10-20", "-5", "BAR")
    app = AppTest.from_function(page, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    app.button(key=button).click().run()
    assert not app.exception, app.exception
    assert (app.session_state[keys[0]], app.session_state[keys[1]]) == (date(2024, 3, 5), date(2026, 10, 20))
