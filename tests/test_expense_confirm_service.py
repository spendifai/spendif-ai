"""The ledger side of the expense confirmation (spec 32, 4.2 and 5): the
search among the outflows, what is written on the movement, the "always like
this?" question and the model's one short call. Synthetic data only.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import CategoryRule, Transaction, create_tables, get_session
from services.expense_confirm_service import ExpenseConfirmService

TODAY = date(2026, 10, 25)


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'e.db'}"))


def _add(engine, tx_id, amount, day, desc="ESSELUNGA", tx_type="expense", checked=False,
         category="Alimentari", subcategory="Spesa supermercato", source="llm"):
    with get_session(engine) as s:
        s.add(Transaction(
            id=tx_id, date=day, amount=Decimal(amount), description=desc, source_file="f",
            account_label="Conto", tx_type=tx_type, category=category, subcategory=subcategory,
            category_source=source, human_validated=checked))
        s.commit()


def _ids(cands):
    return sorted(c.tx_id for c in cands)


def test_outflows_by_amount_and_dates(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    _add(engine, "in", "23.40", "2026-10-12", tx_type="income")         # an inflow
    _add(engine, "far", "-23.40", "2026-10-20")                         # out of the window
    _add(engine, "other", "-23.41", "2026-10-12")                       # another amount
    _add(engine, "giro", "-23.40", "2026-10-12", tx_type="internal_out")  # a giroconto
    _add(engine, "done", "-23.40", "2026-10-12", checked=True)
    svc = ExpenseConfirmService(engine, today=TODAY)
    found = svc.outflows(Decimal("23.40"), Decimal("23.40"), date(2026, 10, 9), date(2026, 10, 15),
                         checked=False)
    assert _ids(found) == ["a"]
    c = found[0]
    assert (c.day, c.amount, c.counterpart.casefold(), c.category, c.subcategory) == (
        date(2026, 10, 12), Decimal("23.40"), "esselunga", "Alimentari", "Spesa supermercato")
    checked = svc.outflows(Decimal("23.40"), Decimal("23.40"), date(2026, 10, 9),
                           date(2026, 10, 15), checked=True)
    assert _ids(checked) == ["done"]


def test_outflows_in_a_range_of_cents(engine):
    _add(engine, "a", "-23.00", "2026-10-12")
    _add(engine, "b", "-23.99", "2026-10-12")
    _add(engine, "c", "-24.00", "2026-10-12")
    found = ExpenseConfirmService(engine).outflows(
        Decimal("23"), Decimal("23.99"), date(2026, 10, 9), date(2026, 10, 15), checked=False)
    assert _ids(found) == ["a", "b"]


def test_the_category_pairs_are_the_expense_side(engine):
    pairs = ExpenseConfirmService(engine).category_pairs()
    assert ("Alimentari", "Spesa supermercato") in pairs
    assert not any(c == "Lavoro dipendente" for c, _ in pairs)


def test_confirm_marks_checked_and_keeps_the_category(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    assert ExpenseConfirmService(engine).confirm("a", None) == []
    with get_session(engine) as s:
        tx = s.get(Transaction, "a")
        assert tx.human_validated and tx.validated_at is not None
        assert (tx.category, tx.category_source) == ("Alimentari", "llm")


def test_confirm_with_a_category_is_by_hand_and_checked(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    ExpenseConfirmService(engine).confirm("a", ("Casa", "Arredamento"))
    with get_session(engine) as s:
        tx = s.get(Transaction, "a")
        assert (tx.category, tx.subcategory, tx.category_source) == ("Casa", "Arredamento", "manual")
        assert tx.human_validated


def test_the_third_choice_by_hand_asks_always(engine):
    for i in range(2):
        _add(engine, f"m{i}", "-5", f"2026-09-0{i + 1}", category="Casa", subcategory="Arredamento",
             source="manual")
    _add(engine, "a", "-23.40", "2026-10-12")
    [q] = ExpenseConfirmService(engine).confirm("a", ("Casa", "Arredamento"))
    assert (q["category"], q["subcategory"], q["direction"], q["n"]) == ("Casa", "Arredamento", "out", 3)


def test_always_yes_uses_the_rule_writer(engine):
    written = []
    svc = ExpenseConfirmService(engine, rule_writer=lambda q: written.append(q) or "the note")
    q = {"description": "ESSELUNGA", "category": "Casa", "subcategory": "Arredamento",
         "direction": "out", "n": 3}
    assert svc.always_yes(q) == "the note"
    assert written == [q]


def test_always_no_is_not_asked_again(engine):
    for i in range(3):
        _add(engine, f"m{i}", "-5", f"2026-09-0{i + 1}", category="Casa", subcategory="Arredamento",
             source="manual")
    svc = ExpenseConfirmService(engine)
    [q] = svc.confirm("m2", ("Casa", "Arredamento"))
    svc.always_no(q)
    assert svc.confirm("m1", ("Casa", "Arredamento")) == []


def test_a_whole_turn_through_the_service(engine):
    _add(engine, "a", "-23.40", "2026-10-24")
    svc = ExpenseConfirmService(engine, today=TODAY)
    msgs, state = svc.start()
    msgs, state = svc.reply(state, "ieri, 23 e 40")
    assert [m.key for m in msgs] == ["expense.found_one"]
    msgs, state = svc.reply(state, "sì")
    with get_session(engine) as s:
        assert s.get(Transaction, "a").human_validated


# ── The model's short call ───────────────────────────────────────────────────

def _fake_model(monkeypatch, answer=None, fail=False):
    calls = []

    def _call(primary, system_prompt, user_prompt, json_schema, **kw):
        calls.append(user_prompt)
        if fail:
            raise RuntimeError("model down")
        return answer, "fake"

    monkeypatch.setattr("core.llm_backends.call_with_fallback", _call)
    return calls


def test_model_pick_returns_the_index(engine, monkeypatch):
    calls = _fake_model(monkeypatch, {"index": 1})
    svc = ExpenseConfirmService(engine, backend=object())
    assert svc.model_pick("rubinetto", ["Casa / Arredamento", "Casa / Manutenzione"]) == 1
    assert "rubinetto" in calls[0] and "Casa / Manutenzione" in calls[0]


@pytest.mark.parametrize("answer", [{"index": -1}, {"index": 5}, {}, None, {"index": "x"}])
def test_model_pick_without_a_valid_answer(engine, monkeypatch, answer):
    _fake_model(monkeypatch, answer)
    assert ExpenseConfirmService(engine, backend=object()).model_pick("x", ["A / B"]) is None


def test_model_pick_when_the_model_fails(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    assert ExpenseConfirmService(engine, backend=object()).model_pick("x", ["A / B"]) is None


def test_no_model_call_while_an_import_runs(engine, monkeypatch):
    from db.repository import create_import_job
    calls = _fake_model(monkeypatch, {"index": 0})
    with get_session(engine) as s:
        create_import_job(s, n_files=1)
        s.commit()
    assert ExpenseConfirmService(engine, backend=object()).model_pick("x", ["A / B"]) is None
    assert calls == []


def test_the_prompt_is_signed():
    from core.prompt_guard import verify_prompt_integrity
    assert verify_prompt_integrity() == []


def test_no_rule_is_created_by_a_confirmation(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    ExpenseConfirmService(engine).confirm("a", ("Casa", "Arredamento"))
    with get_session(engine) as s:
        assert s.query(CategoryRule).count() == 0


def test_last_date_is_the_latest_movement(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    assert svc.last_date() is None
    _add(engine, "a", "-5", "2026-10-09")
    _add(engine, "b", "100", "2026-10-18", tx_type="income")
    assert svc.last_date() == date(2026, 10, 18)
