"""The user's own name (spec 27): decided once, for every movement like this
one, past and future. Equal means same bank text without the words holding
digits, and the identical amount with its sign. Synthetic data only."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.normalizer import own_name_key
from db.models import Base, DescriptionRule, Transaction, _migrate_description_rule_amount
from db import repository

_RATE = "Bonifico eseguito Rapporto 06 77 157302 rata del  mese corrente {ref} {op}"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _tx(s, tx_id, raw, amount, description="Rapporto", **kw):
    s.add(Transaction(id=tx_id, date="2026-10-08", amount=Decimal(amount), raw_description=raw,
                      description=description, tx_type="expense", **kw))
    s.flush()


def _rates(s):
    _tx(s, "r1", _RATE.format(ref="2115219599", op="25502917755117"), "-921.40")
    _tx(s, "r2", _RATE.format(ref="2150975517", op="25502959856721"), "-921.40")
    _tx(s, "r3", _RATE.format(ref="2182565663", op="25502997145523"), "-921.40")
    _tx(s, "cent", _RATE.format(ref="1", op="2"), "-921.41")            # one cent apart
    _tx(s, "refund", _RATE.format(ref="3", op="4"), "921.40")            # same amount, other sign
    _tx(s, "fee", "Commissioni bonifico Rapporto 06 77", "-921.40")       # other bank text


def test_the_key_is_the_raw_text_without_numbers_plus_the_signed_amount():
    a = own_name_key(_RATE.format(ref="1", op="2"), "-921.4")
    b = own_name_key(_RATE.format(ref="9", op="8"), Decimal("-921.40"))
    assert a == b == ("bonifico eseguito rapporto rata del mese corrente", "-921.40")
    assert own_name_key(_RATE.format(ref="1", op="2"), "921.40") != a   # credit, not debit
    assert own_name_key("SALVADANAIO", "5") == ("salvadanaio", "5.00")   # one word is enough here
    assert own_name_key("12345 67", "5") is None


def test_same_movements_are_only_the_equal_ones(session):
    _rates(session)
    assert sorted(repository.same_movements(session, "r1")) == ["r2", "r3"]


def test_one_name_for_all_the_equal_movements(session):
    _rates(session)
    rule, renamed = repository.create_own_name(session, "r1", "mutuo  casa")
    assert sorted(renamed) == ["r1", "r2", "r3"]
    assert {session.get(Transaction, i).description for i in renamed} == {"Mutuo Casa"}
    for other in ("cent", "refund", "fee"):
        assert session.get(Transaction, other).description == "Rapporto"
    assert session.get(Transaction, "r2").raw_description.startswith("Bonifico eseguito Rapporto")
    assert rule.cleaned_description == "Mutuo Casa" and rule.amount == Decimal("-921.40")
    assert repository.own_names(session) == {
        ("bonifico eseguito rapporto rata del mese corrente", "-921.40"): "Mutuo Casa"}


def test_the_own_names_are_not_review_replacements(session):
    _rates(session)
    repository.create_own_name(session, "r1", "Mutuo casa")
    assert repository.get_description_rules(session) == []
    listed = repository.get_own_names(session)
    assert [(o["name"], o["n"]) for o in listed] == [("Mutuo Casa", 3)]


def test_the_same_text_can_carry_one_name_per_amount(session):
    _rates(session)
    repository.create_own_name(session, "r1", "Mutuo casa")
    repository.create_own_name(session, "cent", "Mutuo garage")
    assert len(repository.own_names(session)) == 2


def test_import_names_the_movement_before_the_model(monkeypatch):
    import csv
    import io

    from core import orchestrator
    from core.categorizer import CategorizationResult
    from core.models import CategorySource, Confidence, DocumentType, SignConvention
    from core.orchestrator import ProcessingConfig, process_file
    from core.schemas import DocumentSchema
    from tests.test_giroconto_rule import _TAXONOMY

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    w.writerow(["08/11/2026", _RATE.format(ref="7", op="8"), "-921,40"])   # own name
    w.writerow(["09/11/2026", _RATE.format(ref="5", op="6"), "-100,00"])   # same text, other amount
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="conto", confidence=Confidence.high,
        confidence_score=1.0,
    )
    sent: list[str] = []

    def _clean(txs, **kw):
        sent.extend(t["raw_description"] for t in txs)
        for t in txs:
            t["description"] = "MODEL"
        return txs

    def _categorize(*args, **kw):
        txs = kw.get("transactions", args[0] if args else [])
        return [CategorizationResult("Altro", "", Confidence.high, CategorySource.llm) for _ in txs]

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", _clean)
    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)

    sig = "bonifico eseguito rapporto rata del mese corrente"
    result = process_file(
        buf.getvalue().encode("utf-8"), "conto.csv", ProcessingConfig(), _TAXONOMY, [],
        own_names={(sig, "-921.40"): "Mutuo Casa"},
        known_signatures={sig: "Mutuo Casa"},   # the renamed movements are the oldest
    )
    by_amount = {str(t["amount"]): t for t in result.transactions}
    named = next(t for a, t in by_amount.items() if "921" in a)
    other = next(t for a, t in by_amount.items() if "100" in a)
    assert named["description"] == "Mutuo Casa"
    assert named["raw_description"].startswith("Bonifico eseguito Rapporto")
    # The other amount does not borrow the own name through the signature.
    assert other["description"] == "MODEL"
    assert sent == [other["raw_description"]]


def test_migration_rebuilds_the_table_and_keeps_the_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE description_rule (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "raw_pattern TEXT NOT NULL, match_type VARCHAR(10) NOT NULL, "
            "cleaned_description TEXT NOT NULL, created_at DATETIME, "
            "UNIQUE(raw_pattern, match_type))"))
        conn.execute(text("INSERT INTO description_rule (raw_pattern, match_type, cleaned_description) "
                          "VALUES ('ADDEBITO SDD', 'contains', 'Assicurazione')"))
        conn.commit()
    _migrate_description_rule_amount(engine)
    _migrate_description_rule_amount(engine)  # idempotent
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(description_rule)"))]
        rows = conn.execute(text("SELECT raw_pattern, cleaned_description FROM description_rule")).fetchall()
    assert "amount" in cols
    assert rows == [("ADDEBITO SDD", "Assicurazione")]


_MUTUO = "Addebito banca popolare di sondrio incasso rate mutui  - crediti spec Addebito nº {n}"


def _variable_rates(s):
    for i, amount in enumerate(("-726.34", "-727.55", "-728.76")):
        _tx(s, f"m{i}", _MUTUO.format(n=f"20250{i}1000014"), amount,
            description="Banca Popolare Di Sondrio")
    _tx(s, "back", _MUTUO.format(n="9"), "726.34", description="Banca Popolare Di Sondrio")  # credit


def test_an_installment_that_changes_is_seen(session):
    _variable_rates(session)
    assert repository.same_movements(session, "m0") == []
    assert sorted(t.id for t in repository.same_text_other_amounts(session, "m0")) == ["m1", "m2"]


def test_the_sign_only_rule_names_every_installment_and_the_future_ones(session):
    from core.normalizer import own_name_for
    _variable_rates(session)
    rule, renamed = repository.create_own_name(session, "m0", "Mutuo casa", any_amount=True)
    assert sorted(renamed) == ["m0", "m1", "m2"]
    assert session.get(Transaction, "back").description == "Banca Popolare Di Sondrio"  # other sign
    assert rule.amount is None and rule.direction == "out"
    names = repository.own_names(session)
    assert own_name_for(names, _MUTUO.format(n="2026"), "-744.57") == "Mutuo Casa"
    assert own_name_for(names, _MUTUO.format(n="2026"), "744.57") is None
    listed = repository.get_own_names(session)
    assert listed[0]["amount"] is None and listed[0]["n"] == 3


def test_the_identical_amount_wins_over_the_sign_only(session):
    from core.normalizer import own_name_for
    _variable_rates(session)
    repository.create_own_name(session, "m0", "Mutuo casa", any_amount=True)
    repository.create_own_name(session, "m1", "Rata di febbraio")
    names = repository.own_names(session)
    assert own_name_for(names, _MUTUO.format(n="1"), "-727.55") == "Rata Di Febbraio"
    assert own_name_for(names, _MUTUO.format(n="1"), "-800.00") == "Mutuo Casa"


def test_renaming_all_spreads_the_category_of_the_renamed_movement(session):
    _variable_rates(session)
    ref = session.get(Transaction, "m0")
    ref.category, ref.subcategory, ref.category_source = "Casa", "Mutuo", "manual"
    session.flush()
    _rule, renamed = repository.create_own_name(session, "m0", "Mutuo casa", any_amount=True)
    repository.spread_own_name_category(session, "m0", renamed)
    for i in ("m1", "m2"):
        tx = session.get(Transaction, i)
        assert (tx.category, tx.subcategory, tx.category_source) == ("Casa", "Mutuo", "rule")
    # The user's choice became a rule on the name: the next imports get it too.
    assert any(r.pattern == "Mutuo Casa" and r.category == "Casa"
               for r in repository.get_category_rules(session))
