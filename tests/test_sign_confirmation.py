"""When to ask somebody about the direction of their own money, and when not to.

The direction is measured on the file: the more frequent sign is spending.
How the person uses the account ("payments": ci paghi, "in_and_out": ci
entrano e escono soldi) decides only whether that verdict can be applied in
silence. Their own answer for a format, once given, wins for ever.

The model is not one of the voices any more. It sees column names and a few
rows, and on 2026-10-19 it took an American Express export for a bank
account; under the old rule its guess decided whether the measure was even
consulted, and 44 of 52 charges came in as income without a question.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.orchestrator import _apply_account_usage_to_sign, _sign_needs_a_question


class _Schema:
    """Only the fields the decision reads."""

    def __init__(self, spending_positive=None, conclusive=True, pos=None, sealed=False):
        self.sign_deterministic_verdict = spending_positive
        self.sign_measure_conclusive = conclusive
        self.invert_sign = bool(spending_positive)
        if pos is None and spending_positive is not None:
            pos = 0.85 if spending_positive else 0.15
        self.positive_ratio = pos
        self.negative_ratio = None if pos is None else 1 - pos
        self.user_confirmed = sealed


def test_the_seal_outranks_everything():
    """Asking twice teaches people that their answers do not stick."""
    for usage in ("payments", "in_and_out", None):
        assert _sign_needs_a_question(_Schema(True, conclusive=False, sealed=True), usage) is False


def test_no_measure_no_question():
    """A reused schema, or debit/credit columns: there is nothing to settle."""
    assert _sign_needs_a_question(_Schema(None), None) is False
    assert _sign_needs_a_question(None, "payments") is False


def test_no_majority_always_asks():
    for usage in ("payments", "in_and_out", None):
        assert _sign_needs_a_question(_Schema(True, conclusive=False), usage) is True


def test_an_account_never_described_asks_once_per_new_format():
    assert _sign_needs_a_question(_Schema(True), None) is True
    assert _sign_needs_a_question(_Schema(False), None) is True


def test_a_card_that_looks_like_one_is_silent():
    """The Amex case: 85% positive, described as an account people pay with."""
    assert _sign_needs_a_question(_Schema(True, pos=0.85), "payments") is False
    assert _sign_needs_a_question(_Schema(False, pos=0.10), "payments") is False


def test_a_card_that_does_not_look_like_one_asks():
    """Below two to one the file is not the account the person described."""
    assert _sign_needs_a_question(_Schema(True, pos=0.60), "payments") is True


def test_a_current_account_writing_expenses_negative_is_silent():
    assert _sign_needs_a_question(_Schema(False, pos=0.30), "in_and_out") is False


def test_a_current_account_with_a_positive_majority_asks_and_is_left_as_written():
    schema = _Schema(True, pos=0.70)
    _apply_account_usage_to_sign(schema, "in_and_out")
    assert schema.invert_sign is False
    assert _sign_needs_a_question(schema, "in_and_out") is True


def test_the_usage_never_turns_amounts_round_on_its_own():
    """It can leave a file as written, pending a question; it never inverts."""
    for usage in ("payments", "in_and_out", None):
        schema = _Schema(False, pos=0.20)
        _apply_account_usage_to_sign(schema, usage)
        assert schema.invert_sign is False


# ── The seal, once written, has to survive ───────────────────────────────────

@pytest.fixture()
def session():
    from db.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _schema(**kwargs):
    from core.models import Confidence, DocumentType, SignConvention
    from core.schemas import DocumentSchema

    base = dict(
        doc_type=DocumentType("credit_card"),
        date_col="Data",
        amount_col="Importo",
        sign_convention=SignConvention("signed_single"),
        date_format="%d/%m/%Y",
        account_label="Amex",
        confidence=Confidence("high"),
        source_identifier="amex.csv",
    )
    base.update(kwargs)
    return DocumentSchema(**base)


def test_the_answer_is_written_and_read_back(session):
    from db import repository

    repository.upsert_document_schema(session, _schema(invert_sign=True, user_confirmed=True))
    session.commit()

    stored = repository.get_document_schema(session, "amex.csv")
    assert stored.user_confirmed is True
    assert stored.invert_sign is True


def test_a_later_import_cannot_unseal_a_format(session):
    """The seal describes the format, not the classification that ran today.

    Without this, the next import of the same bank re-classifies, writes its
    own opinion over the schema, and the question comes back to somebody who
    already answered it.
    """
    from db import repository

    repository.upsert_document_schema(session, _schema(invert_sign=True, user_confirmed=True))
    session.commit()

    # A later import classifies the same format afresh and knows nothing of it.
    repository.upsert_document_schema(session, _schema(invert_sign=True, user_confirmed=False))
    session.commit()

    assert repository.get_document_schema(session, "amex.csv").user_confirmed is True


# ── Answering, and what the answer does ──────────────────────────────────────

def test_flipping_turns_the_whole_import_round(tmp_path, monkeypatch):
    """Amount and label move together, or every later total disagrees."""
    from db import repository
    from db.models import Base, Transaction
    from services.import_service import ImportService

    monkeypatch.setattr(ImportService, "recategorize_batch", lambda self, sha: 0)

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    batch = repository.create_import_batch(
        s, sha256="abc", filename="amex.csv", n_transactions=2, flow_used="flow2",
    )
    for tx_id, amount, tx_type in (("a" * 24, 50, "expense"), ("b" * 24, -20, "income")):
        s.add(Transaction(id=tx_id, batch_id=batch.id, date="2026-01-01",
                          amount=amount, tx_type=tx_type, description="x"))
    repository.upsert_document_schema(s, _schema(invert_sign=False))
    s.commit()

    moved = ImportService(engine).confirm_sign("abc", "amex.csv", flip=True)
    assert moved == 2

    s2 = sessionmaker(bind=engine)()
    rows = {t.id: t for t in s2.query(Transaction).all()}
    assert float(rows["a" * 24].amount) == -50 and rows["a" * 24].tx_type == "income"
    assert float(rows["b" * 24].amount) == 20 and rows["b" * 24].tx_type == "expense"

    stored = repository.get_document_schema(s2, "amex.csv")
    assert stored.invert_sign is True
    assert stored.user_confirmed is True


def test_flipping_categorizes_again_but_keeps_what_the_person_set(tmp_path, monkeypatch):
    """Categories chosen while charges looked like income came from the income
    side ("Rendite finanziarie" on a pizza). After the flip they are chosen
    again; a category the person set by hand stays."""
    from core.categorizer import CategorizationResult
    from core.models import CategorySource, Confidence
    from db import repository
    from db.models import Base, Transaction
    from services.category_service import CategoryService
    from services.import_service import ImportService

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    batch = repository.create_import_batch(
        s, sha256="abc", filename="amex.csv", n_transactions=2, flow_used="flow2",
    )
    s.add(Transaction(id="a" * 24, batch_id=batch.id, date="2026-01-01", amount=15,
                      tx_type="income", description="PANDA CLUB", category="Altro entrate"))
    s.add(Transaction(id="b" * 24, batch_id=batch.id, date="2026-01-02", amount=8,
                      tx_type="income", description="IPER", category="Alimentari",
                      category_source="manual"))
    repository.upsert_document_schema(s, _schema(invert_sign=False))
    s.commit()

    seen = []

    def _fake(self, txs, backend=None, progress_callback=None):
        seen.extend(tx["id"] for tx in txs)
        assert all(float(tx["amount"]) < 0 for tx in txs), "categorized before the flip"
        return [CategorizationResult("Ristorazione", "Asporto", Confidence("high"),
                                     CategorySource("llm")) for _ in txs]

    monkeypatch.setattr(CategoryService, "categorize_many", _fake)
    ImportService(engine).confirm_sign("abc", "amex.csv", flip=True)

    rows = {t.id: t for t in sessionmaker(bind=engine)().query(Transaction).all()}
    assert seen == ["a" * 24]
    assert rows["a" * 24].category == "Ristorazione"
    assert rows["b" * 24].category == "Alimentari"


def test_saying_it_is_already_right_still_settles_the_format(tmp_path):
    """Otherwise the question returns next month to somebody who answered it."""
    from db import repository
    from db.models import Base
    from services.import_service import ImportService

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    repository.create_import_batch(
        s, sha256="abc", filename="amex.csv", n_transactions=0, flow_used="flow2",
    )
    repository.upsert_document_schema(s, _schema(invert_sign=False))
    s.commit()

    assert ImportService(engine).confirm_sign("abc", "amex.csv", flip=False) == 0

    stored = repository.get_document_schema(sessionmaker(bind=engine)(), "amex.csv")
    assert stored.user_confirmed is True
    assert stored.invert_sign is False, "nothing was asked to change"


# ── The card, executed rather than imported ──────────────────────────────────

def _card_script(flip_clicked: bool = False) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    import streamlit as st

    from ui import upload_page

    class _Service:
        calls = []

        def confirm_sign(self, batch_sha256, source_identifier, flip):
            _Service.calls.append((batch_sha256, source_identifier, flip))
            st.session_state["_calls"] = list(_Service.calls)
            return 7

    st.session_state.setdefault("_pending_sign_confirmations", [{
        "filename": "amex.csv",
        "batch_sha256": "abc",
        "source_identifier": "amex.csv",
        "n_transactions": 12,
    }])
    upload_page._render_sign_confirmation(_Service())


def _run_card():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_card_script)
    app.run(timeout=30)
    return app


def test_the_card_asks_one_question_with_two_answers():
    app = _run_card()

    assert not app.exception, f"the card raised: {app.exception}"
    labels = [b.label for b in app.button]
    assert len(labels) == 2, f"expected one question with two answers, got {labels}"


def test_the_card_names_the_file_and_the_size_of_what_it_would_change():
    app = _run_card()

    said = " ".join([w.value for w in app.warning] + [m.value for m in app.markdown])
    assert "amex.csv" in said
    assert "12" in said, "the person is asked to decide without being told how much moves"
    assert "sign." not in said, f"untranslated key on screen: {said!r}"


def test_answering_records_the_answer_against_the_format():
    from ui.i18n import t

    app = _run_card()
    # By label rather than by position, and resolved through the same
    # translation the card used: the suite does not run in one language.
    flip = [b for b in app.button if b.label == t("sign.flip")]
    assert flip, [b.label for b in app.button]

    flip[0].click().run(timeout=30)

    assert not app.exception
    assert app.session_state["_calls"] == [("abc", "amex.csv", True)]
