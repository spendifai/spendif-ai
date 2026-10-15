"""The two sides of a transfer, put together across imports."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.transfer_pairing import Movement, pair_across_accounts
from db.models import InternalTransferLink, Transaction, create_tables, get_session
from services.transfer_service import TransferService


def _m(id, day, amount, account, tx_type="expense"):
    return Movement(id, date(2026, 9, day), Decimal(amount), account, tx_type)


def test_both_sides_read_as_transfer_are_a_sure_pair():
    pairs = pair_across_accounts([
        _m("o", 3, "-500", "A", "internal_out"), _m("i", 4, "500", "B", "internal_in")])
    assert [(p.out_id, p.in_id, p.confidence, p.days) for p in pairs] == [("o", "i", "high", 1)]


def test_one_side_read_as_spending_is_proposed():
    pairs = pair_across_accounts([
        _m("o", 3, "-500", "A", "expense"), _m("i", 6, "500", "B", "internal_in")])
    assert pairs[0].confidence == "medium" and (pairs[0].out_marked, pairs[0].in_marked) == (False, True)


def test_unmarked_sides_pair_only_when_a_day_apart_at_most():
    near = pair_across_accounts([_m("o", 3, "-80", "A"), _m("i", 4, "80", "B", "income")])
    far = pair_across_accounts([_m("o", 3, "-80", "A"), _m("i", 6, "80", "B", "income")])
    assert [p.confidence for p in near] == ["low"] and far == []


@pytest.mark.parametrize("movements", [
    [_m("o", 3, "-500", "A", "internal_out"), _m("i", 3, "500", "A", "internal_in")],   # same account
    [_m("o", 3, "-500", "A", "internal_out"), _m("i", 9, "500", "B", "internal_in")],   # 6 days
    [_m("o", 3, "-500", "A", "internal_out"), _m("i", 3, "499", "B", "internal_in")],   # amount
    [_m("o", 3, "-500", "A", "card_tx"), _m("i", 3, "500", "B", "internal_in")],        # card purchase
])
def test_no_pair(movements):
    assert pair_across_accounts(movements) == []


def test_each_movement_in_one_pair_the_surest_and_closest_first():
    pairs = pair_across_accounts([
        _m("o", 3, "-500", "A", "internal_out"),
        _m("far", 5, "500", "B", "internal_in"),
        _m("near", 3, "500", "C", "internal_in"),
        _m("weak", 3, "500", "D", "income"),
    ])
    assert [(p.out_id, p.in_id) for p in pairs] == [("o", "near")]


def test_new_ids_keep_old_pairs_out():
    movements = [_m("o1", 3, "-500", "A", "internal_out"), _m("i1", 3, "500", "B", "internal_in"),
                 _m("o2", 9, "-70", "A", "internal_out"), _m("i2", 9, "70", "B", "internal_in")]
    assert [p.in_id for p in pair_across_accounts(movements, new_ids={"i2"})] == ["i2"]


# ── Stored movements ─────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _store(engine, *rows):
    with get_session(engine) as s:
        for id, day, amount, account, tx_type in rows:
            s.add(Transaction(id=id, date=f"2026-09-{day:02d}", amount=Decimal(amount),
                              account_label=account, tx_type=tx_type, description="x",
                              source_file="f.csv"))
        s.commit()


def _types(engine):
    with get_session(engine) as s:
        return {t.id: (t.tx_type, t.transfer_pair_id is not None, t.transfer_confidence)
                for t in s.query(Transaction)}


def test_a_sure_pair_is_stored_with_its_link(engine):
    _store(engine, ("o", 3, "-500", "A", "internal_out"), ("i", 4, "500", "B", "internal_in"))
    TransferService(engine).pair_new({"i"})
    assert _types(engine) == {"o": ("internal_out", True, "high"), "i": ("internal_in", True, "high")}
    with get_session(engine) as s:
        assert s.query(InternalTransferLink).count() == 1


def test_a_proposed_pair_keeps_the_types_until_confirmed(engine):
    _store(engine, ("o", 3, "-500", "A", "expense"), ("i", 4, "500", "B", "internal_in"))
    TransferService(engine).pair_new({"o"})
    assert _types(engine)["o"] == ("expense", True, "medium")


def test_the_first_run_pairs_the_whole_ledger_once(engine):
    _store(engine, ("o", 3, "-500", "A", "internal_out"), ("i", 4, "500", "B", "internal_in"))
    svc = TransferService(engine)
    assert len(svc.pair_all_once()) == 1
    _store(engine, ("o2", 9, "-70", "A", "internal_out"), ("i2", 9, "70", "B", "internal_in"))
    assert svc.pair_all_once() == []


# ── The workflow step ────────────────────────────────────────────────────────

from core.transfer_pairing import destination_key  # noqa: E402

TODAY = date(2026, 10, 14)   # window: June to September 2026


@pytest.mark.parametrize("desc, raw, key", [
    ("Bonifico a  Rossi Mario", "", "BONIFICO A ROSSI MARIO"),
    ("Bonifico a Rossi", "BON. IT60X0542811101000000123456 ROSSI", "IT60X0542811101000000123456"),
])
def test_destination_key_prefers_the_iban(desc, raw, key):
    assert destination_key(desc, raw) == key


def test_view_orders_proposals_first_and_counts_what_is_older(engine):
    _store(engine,
           ("o1", 3, "-500", "A", "internal_out"), ("i1", 3, "500", "B", "internal_in"),   # sure
           ("o2", 10, "-80", "A", "expense"), ("i2", 10, "80", "B", "income"),             # weak
           ("s1", 20, "-40", "A", "internal_out"))                                          # one side
    svc = TransferService(engine)
    svc.pair_all()
    view = svc.view(TODAY)
    # the sure pair is settled: counted, never shown
    assert [(p.out_id, p.confidence) for p in view.pairs] == [("o2", "low")]
    assert [x.tx_id for x in view.singles] == ["s1"]
    assert (view.open_count, view.settled, view.older) == (2, 1, 0)


def test_answered_items_are_not_shown_again(engine):
    _store(engine, ("o", 10, "-80", "A", "expense"), ("i", 10, "80", "B", "income"),
           ("s1", 20, "-40", "A", "internal_out"))
    svc = TransferService(engine)
    svc.pair_all()
    svc.decide("o", "i", "yes")
    svc.answer(svc.view(TODAY).singles[0].key, "untracked")
    view = svc.view(TODAY)
    assert (view.pairs, view.singles, view.settled) == ([], [], 2)


def test_yes_makes_a_transfer_and_no_is_never_proposed_again(engine):
    _store(engine, ("o", 10, "-80", "A", "expense"), ("i", 10, "80", "B", "income"),
           ("o2", 12, "-90", "A", "expense"), ("i2", 12, "90", "B", "income"))
    svc = TransferService(engine)
    svc.pair_all()
    svc.decide("o", "i", "yes")
    svc.decide("o2", "i2", "no")
    types = _types(engine)
    assert types["o"][0] == "internal_out" and types["i"][0] == "internal_in"
    assert types["o2"] == ("expense", False, None)
    assert svc.pair_all() == []                      # the rejected pair stays apart
    assert svc.view(TODAY).open_count == 0


def test_other_person_turns_the_transfer_into_spending_now_and_later(engine):
    _store(engine, ("s1", 20, "-40", "A", "internal_out"))
    svc = TransferService(engine)
    key = svc.view(TODAY).singles[0].key
    assert svc.answer(key, "other_person") == 1
    assert _types(engine)["s1"][0] == "expense"
    _store(engine, ("s2", 25, "-60", "A", "internal_out"))   # next import, same description
    assert svc.apply_destinations({"s2"}) == 1
    assert _types(engine)["s2"][0] == "expense"


def test_an_account_not_followed_stays_a_transfer_and_is_not_asked_again(engine):
    _store(engine, ("s1", 20, "-40", "A", "internal_out"))
    svc = TransferService(engine)
    svc.answer(svc.view(TODAY).singles[0].key, "untracked")
    assert _types(engine)["s1"][0] == "internal_out"
    assert svc.view(TODAY).open_count == 0


def test_items_before_the_window_are_only_counted(engine):
    with get_session(engine) as s:
        s.add(Transaction(id="old", date="2026-01-05", amount=Decimal("-40"), account_label="A",
                          tx_type="internal_out", description="x", source_file="f.csv"))
        s.commit()
    view = TransferService(engine).view(TODAY)
    assert view.singles == [] and view.older == 1
