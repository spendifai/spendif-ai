"""The reference menu of "Edit several movements": one entry per counterpart
and direction, most movements first, giroconti only when asked. Synthetic."""
from decimal import Decimal

from db.models import Transaction, create_tables, get_engine, get_session
from services.transaction_service import TransactionService


def test_groups_by_counterpart_most_movements_first(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'l.db'}")
    create_tables(engine)
    rows = [
        ("a1", "2026-01-01", "-5", "Bar Sport", "expense"),
        ("a2", "2026-02-01", "-6", "BAR SPORT", "expense"),      # same counterpart
        ("a3", "2026-03-01", "-7", "bar  sport", "expense"),
        ("b1", "2026-01-01", "-900", "Mutuo Casa", "expense"),
        ("c1", "2026-01-02", "-50", "Ristorante Uno", "expense"),
        ("c2", "2026-01-03", "-60", "Ristorante Uno", "expense"),
        ("r1", "2026-01-04", "6", "Bar Sport", "income"),         # other direction
        ("g1", "2026-10-15", "0.02", "Addebito In Cc Salvo Buon Fine", "internal_in"),
    ]
    with get_session(engine) as s:
        for i, d, a, desc, typ in rows:
            s.add(Transaction(id=i, date=d, amount=Decimal(a), description=desc, tx_type=typ))
        s.commit()
    svc = TransactionService(engine)
    groups = svc.counterpart_groups()
    assert [(g["tx"].description, g["n"]) for g in groups] == [
        ("Bar Sport", 3), ("Ristorante Uno", 2), ("Mutuo Casa", 1), ("Bar Sport", 1)]
    assert groups[0]["tx"].id == "a3"                            # the most recent
    assert not any(g["giroconto"] for g in groups)
    with_giro = svc.counterpart_groups(with_giroconti=True)
    assert any(g["giroconto"] and g["tx"].id == "g1" for g in with_giro)


def test_only_the_most_recent_movements_count(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'l.db'}")
    create_tables(engine)
    with get_session(engine) as s:
        for i in range(5):
            s.add(Transaction(id=f"old{i}", date=f"2023-01-0{i + 1}", amount=Decimal("-5"),
                              description="Vecchio Bar", tx_type="expense"))
        for i in range(2):
            s.add(Transaction(id=f"new{i}", date=f"2026-10-0{i + 1}", amount=Decimal("-5"),
                              description="Bar Nuovo", tx_type="expense"))
        s.commit()
    groups = TransactionService(engine).counterpart_groups(recent=3)
    assert [(g["tx"].description, g["n"]) for g in groups] == [("Bar Nuovo", 2), ("Vecchio Bar", 1)]


def test_places_where_everything_is_bought_stay_out(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'l.db'}")
    create_tables(engine)
    with get_session(engine) as s:
        s.add(Transaction(id="m", date="2026-10-01", amount=Decimal("-30"), description="Amzn Mktp De",
                          tx_type="expense", category_source="multi"))
        s.add(Transaction(id="e", date="2026-10-01", amount=Decimal("-5"), description="Bar Sport",
                          tx_type="expense", category_source="llm"))
        s.commit()
    groups = TransactionService(engine).counterpart_groups()
    assert [g["tx"].id for g in groups] == ["e"]


def test_the_search_box_ignores_case_accents_and_punctuation():
    match = TransactionService.text_matches
    assert match("esselunga", "ESSELUNGA S.P.A.")
    assert match("caffe", "Caffè Milano")
    assert match("ristor", "Pizzeria Leone", "Ristorazione")
    assert not match("coop", "Esselunga", "Alimentari")
    assert match("", "anything")
