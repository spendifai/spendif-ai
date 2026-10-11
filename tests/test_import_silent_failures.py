"""Tests for imports that failed while looking successful.

Covers:
  - parse_date_safe: month names written out in the app's languages
    ("02 settembre 2026" with "%d %B %Y" parsed nothing before)
  - compute_confidence_score: one column in two roles caps the score below
    the auto-import threshold
  - prompt templates: every placeholder is a plain field name, so .format()
    cannot raise on a literal JSON example left unescaped
  - process_file: a fresh schema that parses under half the rows goes to
    user review instead of being auto-imported with zero movements
"""
from __future__ import annotations

import csv
import io
import json
import string
from datetime import date
from pathlib import Path

import pytest

import core.orchestrator as orchestrator
from core.categorizer import TaxonomyConfig
from core.classifier import ROLE_CONFLICT_SCORE_CAP, compute_confidence_score
from core.models import Confidence, DocumentType, SignConvention
from core.normalizer import parse_date_safe
from core.orchestrator import ProcessingConfig, process_file
from core.schemas import DocumentSchema

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ── Written month names ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value, fmt, expected",
    [
        ("02 settembre 2026", "%d %B %Y", date(2026, 9, 2)),
        ("2 set 2026", "", date(2026, 9, 2)),
        ("02-ago-2026", "%d-%b-%Y", date(2026, 8, 2)),
        ("1 März 2026", "", date(2026, 3, 1)),
        ("15 févr. 2026", "", date(2026, 2, 15)),
        ("3 diciembre 2026", "", date(2026, 12, 3)),
        ("September 5, 2026", "", date(2026, 9, 5)),
    ],
)
def test_written_month_dates_parse(value, fmt, expected):
    assert parse_date_safe(value, fmt) == expected


@pytest.mark.parametrize("value", ["Grocery store", "mar 2026", "32 settembre 2026"])
def test_text_that_is_not_a_full_date_stays_unparsed(value):
    assert parse_date_safe(value, "%d %B %Y") is None


def test_numeric_dates_unchanged():
    assert parse_date_safe("02/09/2026", "%d/%m/%Y") == date(2026, 9, 2)


# ── Column role conflict ──────────────────────────────────────────────────────

def _complete_schema(**overrides) -> dict:
    schema = {
        "date_col": "Data operazione",
        "date_accounting_col": "Data valuta",
        "debit_col": "Uscite",
        "credit_col": "Entrate",
        "description_col": "Descrizione",
        "sign_convention": "debit_credit_signed",
        "doc_type": "bank_account",
        "account_label": "bank",
    }
    schema.update(overrides)
    return schema


def test_complete_schema_scores_full():
    assert compute_confidence_score(_complete_schema()) == 1.0


def test_description_on_date_column_is_capped():
    score = compute_confidence_score(_complete_schema(description_col="Data operazione"))
    assert score == ROLE_CONFLICT_SCORE_CAP
    assert score < ProcessingConfig().confidence_threshold


def test_amount_on_date_column_is_capped():
    score = compute_confidence_score(_complete_schema(debit_col="Data valuta"))
    assert score < ProcessingConfig().confidence_threshold


# ── Prompt templates ──────────────────────────────────────────────────────────

def _templates():
    for path in sorted(PROMPTS_DIR.glob("*.json")):
        if path.name == "prompt_hashes.json":
            continue
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(value, str) and "template" in key:
                yield pytest.param(value, id=f"{path.stem}.{key}")


@pytest.mark.parametrize("template", list(_templates()))
def test_template_placeholders_are_field_names(template):
    fields = {f for _, f, _, _ in string.Formatter().parse(template) if f is not None}
    assert all(f.isidentifier() for f in fields), f"bad placeholders: {fields}"
    template.format(**{f: "x" for f in fields})


# ── Auto-import parse-rate guard ──────────────────────────────────────────────

def _statement_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    for i in range(1, 9):
        w.writerow([f"giorno {i}", f"Movimento {i}", f"-{i}0,00"])
    return buf.getvalue().encode("utf-8")


class _NoLLM:
    """Any call means the test reached a model it should not need."""

    def __getattr__(self, name):
        raise RuntimeError(f"unexpected LLM call: {name}")


def test_schema_parsing_no_rows_goes_to_review(monkeypatch):
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
        _statement_csv(),
        "statement.csv",
        ProcessingConfig(),
        TaxonomyConfig(expenses={"Altro": ["Altro"]}, income={"Altro entrate": ["Altro"]}),
        [],
    )

    assert result.needs_schema_review is True
    assert result.transactions == []
    assert result.total_file_rows == 8
    assert result.errors and "0 of 8" in result.errors[0]
