"""When to ask somebody about the direction of their own money, and when not to.

Three sources decide it. The person's own answer, once given, wins for ever.
Failing that, two independent readings are compared: the model's and the
measurement on the data. Agreement means silence. Only disagreement is worth
an interruption.

The trigger is deliberately not a confidence threshold. A threshold is a dial
somebody has to tune and defend, and it was the wrong instrument here: the
question is not "how sure are we" but "do our two ways of knowing contradict
each other".
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.orchestrator import _sign_needs_a_question


class _Schema:
    """Only the fields the decision reads."""

    def __init__(self, model=None, data=None, sealed=False):
        self.sign_llm_verdict = model
        self.sign_deterministic_verdict = data
        self.user_confirmed = sealed


def test_agreement_is_silent():
    assert _sign_needs_a_question(_Schema(model=True, data=True)) is False
    assert _sign_needs_a_question(_Schema(model=False, data=False)) is False


def test_disagreement_is_the_whole_trigger():
    assert _sign_needs_a_question(_Schema(model=True, data=False)) is True
    assert _sign_needs_a_question(_Schema(model=False, data=True)) is True


def test_the_seal_outranks_a_disagreement():
    """Asking twice teaches people that their answers do not stick."""
    assert _sign_needs_a_question(_Schema(model=True, data=False, sealed=True)) is False


def test_one_reading_alone_is_not_a_disagreement():
    """A reused schema, or a file whose layout carries the direction already.

    Nothing contradicts anything here, and inventing a question would be
    noise of exactly the kind a confidence threshold produces.
    """
    assert _sign_needs_a_question(_Schema(model=True, data=None)) is False
    assert _sign_needs_a_question(_Schema(model=None, data=True)) is False
    assert _sign_needs_a_question(_Schema()) is False
    assert _sign_needs_a_question(None) is False


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

def test_flipping_turns_the_whole_import_round(tmp_path):
    """Amount and label move together, or every later total disagrees."""
    from db import repository
    from db.models import Base, Transaction
    from services.import_service import ImportService

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
