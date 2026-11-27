"""The classifier JSON schemas must force the model to answer on every column role.

Why these tests exist: llama.cpp turns the JSON schema into a grammar, and a grammar lets the
model skip optional properties. Gemma 3 12B skipped debit_col and credit_col on every file with
separate debit/credit columns, so the direction of the amounts was decided by row density alone
and came out reversed on accounts with more incomes than expenses. The column roles are now
required but nullable: the model must write them, and "no such column" is written as null.
"""

from __future__ import annotations

import pytest

from core.models import Confidence, DocumentType, SignConvention
from core.schemas import DocumentSchema, step2_json_schema

COLUMN_ROLE_FIELDS = ("amount_col", "debit_col", "credit_col", "date_accounting_col", "currency_col")


def _single_step_schema() -> dict:
    """JSON schema of the single-step classification, built as classify_document builds it."""
    placeholder = DocumentSchema(
        doc_type=DocumentType.unknown, date_col="", amount_col="", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="x", confidence=Confidence.low,
    )
    return placeholder.llm_json_schema()


@pytest.mark.parametrize("schema_builder", [_single_step_schema, step2_json_schema], ids=["single_step", "multi_step_2"])
@pytest.mark.parametrize("field_name", COLUMN_ROLE_FIELDS)
def test_column_role_is_required_and_nullable(schema_builder, field_name):
    """Every column role must be present in the answer and may be null."""
    schema = schema_builder()
    assert field_name in schema["required"]
    assert "null" in schema["properties"][field_name]["type"]
