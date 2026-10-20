"""The transfers step: pairs and one-sided transfers, answered on the page."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import Transaction, create_tables, get_session
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _keep_language():
    before = get_language()
    yield
    set_language(before)


def _page(db_path: str) -> None:
    from sqlalchemy import create_engine

    from ui.i18n import set_language
    from ui.transfers_page import render_transfers_page

    set_language("en")
    render_transfers_page(create_engine(f"sqlite:///{db_path}"))


def _ledger(tmp_path, rows):
    from datetime import date, timedelta
    db_path = tmp_path / "ledger.db"
    engine = create_engine(f"sqlite:///{db_path}")
    create_tables(engine)
    base = date.today().replace(day=1) - timedelta(days=20)   # inside the window
    with get_session(engine) as s:
        for id, days, amount, account, tx_type in rows:
            s.add(Transaction(id=id, date=(base + timedelta(days=days)).isoformat(),
                              amount=Decimal(amount), account_label=account, tx_type=tx_type,
                              description=f"desc {id}", source_file="f.csv"))
        s.commit()
    from services.transfer_service import TransferService
    TransferService(engine).pair_all()
    return db_path


def test_nothing_to_look_at(tmp_path):
    at = AppTest.from_function(_page, kwargs={"db_path": str(_ledger(tmp_path, []))}).run(timeout=30)
    assert not at.exception, at.exception
    assert "No transfers to look at" in at.success[0].value


def test_pairs_and_single_sides_are_shown_with_their_reasons(tmp_path):
    db = _ledger(tmp_path, [("o", 0, "-500", "A", "internal_out"), ("i", 1, "500", "B", "income"),
                            ("s", 2, "-40", "A", "internal_out")])
    at = AppTest.from_function(_page, kwargs={"db_path": str(db)}).run(timeout=30)
    assert not at.exception, at.exception
    headers = [h.value for h in at.subheader]
    assert headers == ["Pairs to approve", "With one side only"]
    from services.transfer_service import TransferService
    from ui.transfers_page import pair_reason
    set_language("en")
    p = TransferService(create_engine(f"sqlite:///{db}")).view().pairs[0]
    assert pair_reason(p) == "Medium: same amount, one day later; one of the two was income"
