"""A code is not a description, and a movement without one gets no name.

Spec 13 par. 4 and 4.1 (founder, 2026-10-25). Satispay: the transaction ID
(a UUID, half hex letters, different on every row) outranked the merchant
name, which repeats; the import drops UUIDs, so 497 movements came in with
an empty description. The cleaner then gave each one the same name, taken
from the examples of the history, and the history its category.
"""
from __future__ import annotations

import csv
import io
import uuid

import pandas as pd

import core.orchestrator as orchestrator
from core.categorizer import TaxonomyConfig
from core.classifier import _run_step0_analysis
from core.description_cleaner import clean_descriptions_batch
from core.history_engine import HistoryCache
from core.models import Confidence, DocumentType, SignConvention
from core.orchestrator import ProcessingConfig, process_file
from core.schemas import DocumentSchema


class _NoLLM:
    """Any call means the code reached a model it should not need."""

    def __getattr__(self, name):
        raise RuntimeError(f"unexpected LLM call: {name}")


def test_a_transaction_id_is_not_the_description():
    n = 40
    df = pd.DataFrame({
        "ID (Comunicalo all'Assistenza Clienti in caso di problemi)":
            [str(uuid.uuid5(uuid.NAMESPACE_URL, str(i))) for i in range(n)],
        "Data": [f"{1 + i % 28:02d}/03/2026" for i in range(n)],
        # merchants repeat: 8 names over 40 rows, a fifth of the ID's values
        "Nome": [f"Bar {['Roma', 'Sole', 'Luna', 'Centrale'][i % 4]} {i % 8}"
                 for i in range(n)],
        "Importo": [f"-{2 + i},40" for i in range(n)],
    })
    result = _run_step0_analysis(list(df.columns), df_raw=df)
    assert result.description_cols == ["Nome"]
    assert result.description_col == "Nome"


def test_a_varying_reference_without_spaces_can_still_describe():
    """Only all-distinct values of one length make a code: merchant names
    without spaces and of different lengths stay a description."""
    n = 30
    df = pd.DataFrame({
        "Data": [f"{1 + i % 28:02d}/03/2026" for i in range(n)],
        "Esercente": [f"{['Esselunga', 'Lidl', 'Coop', 'Autogrill', 'Ikea'][i % 5]}{i}"
                      for i in range(n)],
        "Importo": [f"-{10 + i},00" for i in range(n)],
    })
    result = _run_step0_analysis(list(df.columns), df_raw=df)
    assert result.description_cols == ["Esercente"]


def test_an_empty_description_never_reaches_the_cleaner():
    txs = [{"id": "a", "amount": -3, "description": "", "raw_description": None}]
    out = clean_descriptions_batch(txs, llm_backend=_NoLLM())
    assert out[0]["description"] == ""


def _statement(rows_without_description: int) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    for i in range(1, 9):
        desc = "" if i <= rows_without_description else f"Negozio {i}"
        w.writerow([f"0{i}/03/2026", desc, f"-{i}0,00"])
    return buf.getvalue().encode("utf-8")


def test_schema_leaving_rows_without_description_goes_to_review(monkeypatch):
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account,
        date_col="Data",
        amount_col="Importo",
        description_col="Descrizione",
        sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y",
        account_label="bank",
        confidence=Confidence.high,
        confidence_score=1.0,
    )
    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: _NoLLM())
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)

    result = process_file(
        _statement(rows_without_description=2),
        "statement.csv",
        ProcessingConfig(),
        TaxonomyConfig(expenses={"Altro": ["Altro"]}, income={"Altro entrate": ["Altro"]}),
        [],
    )

    assert result.needs_schema_review is True
    assert result.transactions == []


def test_an_empty_description_gets_nothing_from_the_history():
    cache = HistoryCache.__new__(HistoryCache)
    cache._cache = {"": object()}   # a past movement stored without a name
    assert cache.lookup("") == (None, None, 0.0)
    assert cache.lookup(None) == (None, None, 0.0)
