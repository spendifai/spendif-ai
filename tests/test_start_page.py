"""On an empty registry the app asks how to start (spec 16, 2): be guided,
from the first step, or go to the import. The guided flow opened on an empty
registry starts from "what to load", never from an empty report."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import Transaction, create_tables, get_session
from services.guided_flow_service import STEPS, GuidedFlowService


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "e.db"
    create_tables(create_engine(f"sqlite:///{path}"))
    return path


def _engine(db):
    return create_engine(f"sqlite:///{db}")


def test_an_empty_registry_starts_the_flow_from_the_first_step(db):
    svc = GuidedFlowService(_engine(db))
    closed = svc.closed_steps()
    assert "checklist" not in closed and "import" not in closed
    assert svc.resume() == STEPS[0] == "checklist"


def _page(db_path: str) -> None:  # pragma: no cover - AppTest runs a copy of it
    from sqlalchemy import create_engine
    from ui.i18n import set_language
    from ui.start_page import render_start_page
    set_language("it")
    render_start_page(create_engine(f"sqlite:///{db_path}"))


@pytest.mark.parametrize("button, page", [("start_guided", "guided"), ("start_self", "import")])
def test_two_ways_to_start(db, button, page):
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    assert app.header[0].value == "Benvenuto in Spendif.ai"
    app.button(key=button).click().run()
    assert app.session_state["page"] == page


def test_with_movements_the_page_sends_to_the_home(db):
    with get_session(_engine(db)) as s:
        s.add(Transaction(id="t", date="2026-10-01", amount=Decimal("-5"), description="BAR",
                          source_file="f", account_label="a", tx_type="expense"))
        s.commit()
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    assert app.session_state["page"] == "home"
