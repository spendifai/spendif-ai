"""Checking movements one account at a time (spec 31): where each account
stands, the account proposed, the grid's movements, and the flow driven
through the real page. Synthetic data."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import Base, Transaction, get_session
from services.checking_service import AccountCheck, CheckingService, proposed


def _tx(tx_id, day, amount, account, *, checked=False, tx_type=None, description="Coop"):
    return Transaction(
        id=tx_id, date=day, amount=Decimal(str(amount)), currency="EUR", description=description,
        source_file="f.csv", doc_type="bank_statement", account_label=account,
        tx_type=tx_type or ("expense" if amount < 0 else "income"),
        category="Alimentari", subcategory="Spesa", category_source="llm",
        reconciled=False, to_review=False, human_validated=checked,
    )


def _ledger(tmp_path):
    db = tmp_path / "ledger.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    with get_session(eng) as s:
        s.add_all([
            # POPSO: checked up to 10 March, one gap before, two after
            _tx("p1", "2026-03-01", -10, "POPSO"),
            _tx("p2", "2026-03-05", -20, "POPSO", checked=True),
            _tx("p3", "2026-03-10", -30, "POPSO", checked=True),
            _tx("p4", "2026-03-12", -40, "POPSO"),
            _tx("p5", "2026-03-11", -50, "POPSO"),
            # Amex: never checked
            _tx("a1", "2026-02-01", -5, "Amex"),
            _tx("a2", "2026-02-03", -6, "Amex"),
            # Revolut: all checked
            _tx("r1", "2026-01-01", -1, "Revolut", checked=True),
        ])
        s.commit()
    return db, eng


def test_where_each_account_stands(tmp_path):
    _, eng = _ledger(tmp_path)
    by = {a.account: a for a in CheckingService(eng).accounts()}
    assert by["POPSO"].last_checked == date(2026, 3, 10)
    assert by["POPSO"].start == date(2026, 3, 11)
    assert (by["POPSO"].to_check, by["POPSO"].before) == (2, 1)
    assert by["Amex"].last_checked is None and by["Amex"].start == date(2026, 2, 1)
    assert by["Amex"].to_check == 2
    assert by["Revolut"].to_check == 0


def test_the_account_furthest_behind_is_proposed():
    never = AccountCheck("A", None, date(2026, 1, 1), 3, 0)
    old = AccountCheck("B", date(2025, 1, 1), date(2024, 1, 1), 9, 0)
    even = AccountCheck("C", date(2020, 1, 1), date(2019, 1, 1), 0, 0)
    assert proposed([old, never, even]).account == "A"
    assert proposed([old, even]).account == "B"
    assert proposed([even]) is None


def test_the_grid_has_the_unchecked_movements_oldest_first(tmp_path):
    _, eng = _ledger(tmp_path)
    svc = CheckingService(eng)
    assert [t.id for t in svc.unchecked("POPSO", date(2026, 3, 11))] == ["p5", "p4"]
    assert svc.remaining("POPSO", date(2026, 3, 11)) == 2
    assert [t.id for t in svc.unchecked("POPSO", date(2026, 3, 1))] == ["p1", "p5", "p4"]


def test_one_sided_transfers_of_the_account_only(tmp_path, monkeypatch):
    _, eng = _ledger(tmp_path)
    from services import transfer_service as ts

    class _View:
        singles = [
            ts.SingleRow("x1", date(2026, 3, 12), "POPSO", Decimal("-100"), "k1", "Bonifico", None),
            ts.SingleRow("x2", date(2026, 3, 1), "POPSO", Decimal("-100"), "k2", "Bonifico", None),
            ts.SingleRow("x3", date(2026, 3, 12), "Amex", Decimal("-100"), "k3", "Bonifico", None),
        ]

    monkeypatch.setattr(ts.TransferService, "view", lambda self, today=None: _View())
    found = CheckingService(eng).one_sided_transfers("POPSO", date(2026, 3, 11))
    assert [x.tx_id for x in found] == ["x1"]


# ── The page ─────────────────────────────────────────────────────────────────

def _page(db_path: str) -> None:
    from sqlalchemy import create_engine
    from ui.checking_page import render_checking_page
    render_checking_page(create_engine(f"sqlite:///{db_path}"))


def _button(app, key):
    return next(b for b in app.button if b.key == key)


def test_the_flow_from_the_list_to_an_account_all_checked(tmp_path):
    db, eng = _ledger(tmp_path)
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    # Amex was never checked: it is proposed
    assert app.selectbox(key="check_pick_account").value == "Amex"
    _button(app, "check_next").click().run()
    assert not app.exception, app.exception
    assert app.session_state["_check_phase"] == "grid"
    assert any("**2**" in m.value for m in app.markdown)
    # "All the movements shown are right", confirmed
    _button(app, "check_all").click().run()
    _button(app, "check_all_yes").click().run()
    assert not app.exception, app.exception
    with get_session(eng) as s:
        assert all(s.get(Transaction, i).human_validated for i in ("a1", "a2"))
    # Nothing left, no transfers to verify: back to the list, POPSO proposed
    app.run()
    assert app.session_state["_check_phase"] == "list"
    assert app.selectbox(key="check_pick_account").value == "POPSO"


def test_the_gap_before_the_start_can_be_taken(tmp_path):
    db, _ = _ledger(tmp_path)
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    app.selectbox(key="check_pick_account").set_value("POPSO").run()
    assert not app.exception, app.exception
    assert app.date_input(key="check_pick_since_POPSO").value == date(2026, 3, 11)
    _button(app, "check_gap_start").click().run()
    assert app.date_input(key="check_pick_since_POPSO").value == date(2026, 3, 1)


def test_the_raw_text_toggle_sits_in_the_grid_header(tmp_path):
    db, _ = _ledger(tmp_path)
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    _button(app, "check_next").click().run()
    raw = next(t for t in app.toggle if t.key == "check_show_raw")
    raw.set_value(True).run()
    assert not app.exception, app.exception
