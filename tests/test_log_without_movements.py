"""The log of an import says how the file was read, never what is in it.

WHY IT EXISTS
    The header detection logged the first rows of every file at INFO, so
    merchant, amount and date sat in the log, one movement per line, for the
    first dozens of rows of every import (seen 2026-09-25 in a real import).
    The diagnostics report is built field by field to carry no personal data,
    but somebody asking for help attaches the log, and with it their bank
    statement. The rows above the table are worse: that is where banks put the
    holder's name and the IBAN.

    Finding where the table starts needs densities and counts, not values.
"""
from __future__ import annotations

import csv
import io
import logging

import pytest

# Values that must never appear in the log, whatever the level.
HOLDER = "Zebedeo Quaglierini"
IBAN = "IT60X0542811101000000123456"
MERCHANT = "Pasticceria Ornitorinco"
AMOUNT = "-9876,54"


def _csv_bytes() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Intestatario", HOLDER, ""])
    w.writerow(["IBAN", IBAN, ""])
    w.writerow(["", "", ""])
    w.writerow(["Data", "Descrizione", "Importo"])
    w.writerow(["03/01/2026", MERCHANT, AMOUNT])
    for day in range(4, 20):
        w.writerow([f"{day:02d}/01/2026", f"{MERCHANT} {day}", f"-{day},00"])
    return buf.getvalue().encode("utf-8")


def _xlsx_bytes() -> bytes:
    import pandas as pd

    rows = [["Intestatario", HOLDER, None], ["IBAN", IBAN, None],
            [None, None, None], ["Data", "Descrizione", "Importo"],
            ["03/01/2026", MERCHANT, AMOUNT]]
    rows += [[f"{d:02d}/01/2026", f"{MERCHANT} {d}", f"-{d},00"] for d in range(4, 20)]
    out = io.BytesIO()
    pd.DataFrame(rows).to_excel(out, header=False, index=False)
    return out.getvalue()


@pytest.mark.parametrize("filename,payload", [
    ("estratto.csv", _csv_bytes),
    ("estratto.xlsx", _xlsx_bytes),
])
def test_an_import_logs_no_value_of_the_file(monkeypatch, caplog, filename, payload):
    from core import orchestrator
    from core.categorizer import CategorizationResult
    from core.models import CategorySource, Confidence, DocumentType, SignConvention
    from core.orchestrator import ProcessingConfig, process_file
    from core.schemas import DocumentSchema
    from tests.test_giroconto_rule import _TAXONOMY

    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="conto", confidence=Confidence.high,
        confidence_score=1.0,
    )
    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", lambda txs, **kwargs: txs)
    monkeypatch.setattr(orchestrator, "categorize_batch", lambda transactions, **kwargs: [
        CategorizationResult(category="Spesa", subcategory="Supermercato",
                             confidence=Confidence.high, source=CategorySource.llm)
        for _ in transactions])

    caplog.set_level(logging.DEBUG, logger="SPENDIFY")
    result = process_file(payload(), filename, ProcessingConfig(), _TAXONOMY, [])

    assert result.transactions, "the file must have been read, or the test proves nothing"
    _assert_clean(caplog)


def _assert_clean(caplog):
    log = "\n".join(r.getMessage() for r in caplog.records)
    for value in (HOLDER, "Quaglierini", IBAN, MERCHANT, "Ornitorinco", "9876"):
        assert value not in log, f"{value!r} reached the log"


def test_the_rows_stripped_above_the_table_are_not_logged(caplog):
    """When pandas took a row above the table as header, that row is the holder."""
    import pandas as pd

    from core.normalizer import detect_and_strip_preheader_rows

    rows = [[IBAN, None, None], [None, None, None], ["Data", "Descrizione", "Importo"]]
    rows += [[f"{d:02d}/01/2026", f"{MERCHANT} {d}", AMOUNT] for d in range(1, 29)]
    df = pd.DataFrame(rows, columns=[f"Intestatario {HOLDER}", "Unnamed: 1", "Unnamed: 2"])

    caplog.set_level(logging.DEBUG, logger="SPENDIFY")
    trimmed, n_skipped = detect_and_strip_preheader_rows(df, "estratto.csv")

    assert n_skipped > 0, "the strip must have run, or the test proves nothing"
    assert list(trimmed.columns) == ["Data", "Descrizione", "Importo"]
    _assert_clean(caplog)


def test_the_values_can_be_asked_for_by_hand(monkeypatch):
    from core import normalizer

    assert normalizer._shown(["x"]) == "(hidden)"
    monkeypatch.setattr(normalizer, "_LOG_ROW_VALUES", True)
    assert normalizer._shown(["x"]) == ["x"]
