"""One column, one role.

Two date columns written with the month in full ("02 settembre 2026") used to
be typed as text before the model ran, so both were merged into the
description and the date ended up with two roles. The schema review form then
accepted the same kind of clash without a word. Synthetic data only.
"""

from __future__ import annotations

import pandas as pd

from core.classifier import (
    ROLE_CONFLICT_SCORE_CAP,
    _classify_column_content,
    _run_step0_analysis,
    compute_confidence_score,
    role_conflicts,
)


def _statement() -> pd.DataFrame:
    days = ["02 settembre 2026", "05 settembre 2026", "11 agosto 2026",
            "28 luglio 2026", "03 luglio 2026", "15 giugno 2026"]
    value_days = ["03 settembre 2026", "05 settembre 2026", "12 agosto 2026",
                  "29 luglio 2026", "03 luglio 2026", "16 giugno 2026"]
    return pd.DataFrame({
        "Data operazione": days,
        "Data valuta": value_days,
        "Descrizione": ["Supermercato", "Stipendio", "Farmacia",
                        "Bonifico ricevuto", "Ristorante", "Benzina"],
        "Uscite": ["-45,20", None, "-12,00", None, "-60,00", "-50,00"],
        "Entrate": [None, "1.800,00", None, "200,00", None, None],
    })


# ── Phase 0 types written-month dates as dates ───────────────────────────────

def test_written_month_column_is_a_date():
    assert _classify_column_content(_statement()["Data operazione"]) == "date"


def test_numeric_dates_and_free_text_keep_their_type():
    assert _classify_column_content(pd.Series(["02/09/2026", "05/09/2026"])) == "date"
    assert _classify_column_content(pd.Series(["Supermercato", "Farmacia"])) == "text"


def test_written_month_dates_stay_out_of_the_description():
    df = _statement()
    step0 = _run_step0_analysis(list(df.columns), df)

    assert "Data operazione" not in (step0.description_cols or [])
    assert "Data valuta" not in (step0.description_cols or [])
    assert step0.description_col == "Descrizione"
    assert {step0.date_col, step0.date_accounting_col} == {"Data operazione", "Data valuta"}


# ── The conflict itself ──────────────────────────────────────────────────────

def test_role_conflicts_names_the_column_and_its_roles():
    clash = {"date_col": "Data", "description_col": "Data", "amount_col": "Importo"}
    assert role_conflicts(clash) == {"Data": ["date_col", "description_col"]}
    assert role_conflicts({"date_col": "Data", "amount_col": "Importo"}) == {}


def test_a_clean_two_column_schema_is_not_capped():
    schema = {
        "date_col": "Data operazione", "date_accounting_col": "Data valuta",
        "debit_col": "Uscite", "credit_col": "Entrate", "description_col": "Descrizione",
        "sign_convention": "debit_credit_signed", "doc_type": "bank_account",
        "account_label": "Conto",
    }
    assert compute_confidence_score(schema) > ROLE_CONFLICT_SCORE_CAP


# ── The review form, executed in a real Streamlit runtime ────────────────────

def _review_script(amount_choice: str = "") -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    from types import SimpleNamespace

    import streamlit as st

    from core.models import Confidence, DocumentType, SignConvention
    from core.schemas import DocumentSchema
    from ui import upload_page

    class _Service:
        def get_raw_head(self, *a, **k):
            raise RuntimeError("no preview in this test")

        def get_normalized_preview(self, *a, **k):
            return []

    schema = DocumentSchema(
        doc_type=DocumentType("bank_account"),
        date_col="Data operazione", date_accounting_col="Data valuta",
        amount_col=amount_choice, debit_col="Uscite", credit_col="Entrate",
        description_col="Descrizione",
        sign_convention=SignConvention("debit_credit_signed"),
        date_format="%d/%m/%Y", account_label="Conto", confidence=Confidence.medium,
    )
    st.session_state.setdefault("_pending_schema_reviews", [{
        "filename": "estratto.xlsx",
        "raw_bytes": b"",
        "result": SimpleNamespace(
            doc_schema=schema,
            available_columns=["Data operazione", "Data valuta", "Descrizione",
                               "Uscite", "Entrate"],
        ),
    }])
    upload_page._render_schema_review(_Service(), None)


def _run_review(amount_choice: str):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_review_script, kwargs={"amount_choice": amount_choice})
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def _confirm_button(app):
    return [b for b in app.button if b.proto.type == "primary"][0]


def test_the_form_stops_a_column_with_two_roles():
    app = _run_review(amount_choice="Entrate")

    assert any("Entrate" in e.value for e in app.error)
    assert _confirm_button(app).disabled


def test_the_form_lets_a_clean_schema_through():
    app = _run_review(amount_choice="")

    assert not app.error
    assert not _confirm_button(app).disabled
