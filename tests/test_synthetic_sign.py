"""The sign, over every synthetic file, against known ground truth.

WHY IT EXISTS
    The direction of the amounts is the kind of decision whose damage shows up
    months later, one bank at a time, as somebody noticing that a month of
    expenses is recorded as income. On 2026-10-19 it happened with an American
    Express export: the model took it for a bank account, the measurement on
    the data was never consulted, and 44 of 52 charges came in as income
    without a question.

    The gate can run here, with no model and no GPU, because the generated
    files come with the answer: the manifest carries the document type and the
    .expected.csv carries the signed amount of every row.

WHAT IT ASSERTS
    Never wrong in silence. On every single-amount-column file the direction
    measured on the data is either right, or the import asks. Which of the two
    depends on how the person uses the account; the manifest has no person, so
    its document type stands in for the answer they would give (cards, prepaid
    and cash: "payments"; bank and savings accounts: "in_and_out"), and the
    case of an account never described is checked too.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation

import pytest

from core.classifier import _measure_sign_from_data, _ordered_nonzero_amounts
from core.orchestrator import _apply_account_usage_to_sign, _sign_needs_a_question
from tests.test_synthetic_import import (
    _GENERATED_DIR,
    _ensure_generated_files,
    _load_manifest,
    _run_step0_analysis,
    load_raw_dataframe,
)

# A flipped file does not move the share of negatives a little, it turns it
# inside out (0.8 becomes 0.2). Anything within this band is noise from footer
# rows and unparsed cells; anything outside it is an inversion.
TOLERANCE = 0.15


def _expected_negative_share(filename: str) -> float | None:
    """The share of rows written negative in the ground truth."""
    expected_path = _GENERATED_DIR / (filename.rsplit(".", 1)[0] + ".expected.csv")
    if not expected_path.exists():
        return None

    negative = total = 0
    with expected_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                amount = Decimal(row["amount"])
            except (InvalidOperation, KeyError, TypeError):
                continue
            if amount == 0:
                continue
            total += 1
            if amount < 0:
                negative += 1
    return negative / total if total else None


def _measured_negative_share(values: list[Decimal], invert: bool) -> float:
    signed = [-v for v in values] if invert else values
    nonzero = [v for v in signed if v != 0]
    if not nonzero:
        return 0.0
    return sum(1 for v in nonzero if v < 0) / len(nonzero)


@pytest.fixture(scope="module", autouse=True)
def generated():
    _ensure_generated_files()


# What a person would answer for each kind of document in the corpus.
_USAGE_BY_DOC_TYPE = {
    "credit_card": "payments",
    "debit_card": "payments",
    "prepaid_card": "payments",
    "cash": "payments",
    "bank_account": "in_and_out",
    "savings_account": "in_and_out",
}


class _Measured:
    """The fields of a freshly classified schema the question reads."""

    def __init__(self, result: dict):
        self.user_confirmed = False
        self.invert_sign = bool(result.get("invert_sign"))
        self.sign_deterministic_verdict = result.get("sign_deterministic_verdict")
        self.sign_measure_conclusive = result.get("sign_measure_conclusive")
        self.positive_ratio = result.get("positive_ratio")
        self.negative_ratio = result.get("negative_ratio")


@pytest.fixture(scope="module")
def cases(generated):
    """Every single-amount-column file, measured, with its ground truth."""
    out = []
    for entry in _load_manifest():
        if entry.has_debit_credit_split:
            # Two-column layouts carry the direction structurally: there is no
            # sign to infer, and the decision under test does not run on them.
            continue
        expected_share = _expected_negative_share(entry.filename)
        if expected_share is None:
            continue
        raw = (_GENERATED_DIR / entry.filename).read_bytes()
        df, _enc, _info = load_raw_dataframe(raw, entry.filename)
        step0 = _run_step0_analysis(list(df.columns), df_raw=df)
        if not step0.amount_col:
            continue
        values = _ordered_nonzero_amounts(df, step0.amount_col)
        if not values:
            continue
        result = _measure_sign_from_data(
            {"amount_col": step0.amount_col, "debit_col": None, "credit_col": None,
             "sign_convention": "signed_single", "invert_sign": False},
            df, entry.filename,
        )
        out.append((entry, result, values, expected_share))
    return out


def _outcomes(cases, usage_of):
    """Per file: was the applied direction right, and does the import ask."""
    rows = []
    for entry, result, values, expected in cases:
        usage = usage_of(entry)
        schema = _Measured(result)
        _apply_account_usage_to_sign(schema, usage)
        measured = _measured_negative_share(values, schema.invert_sign)
        right = abs(measured - expected) <= TOLERANCE
        rows.append((entry, right, _sign_needs_a_question(schema, usage), measured, expected))
    return rows


def test_the_corpus_reaches_the_decision(cases):
    """A gate that happens to measure nothing would prove nothing."""
    kinds = {e.doc_type for e, *_ in cases}
    assert {"credit_card", "bank_account"} <= kinds, kinds
    assert len(cases) >= 15, f"only {len(cases)} files reached the sign decision"


def _silent_errors(cases, usage_of) -> list[str]:
    return [
        f"{e.filename} ({e.doc_type}): negatives {measured:.2f} against "
        f"{expected:.2f}, imported without a question"
        for e, right, asks, measured, expected in _outcomes(cases, usage_of)
        if not right and not asks
    ]


def test_never_wrong_in_silence_when_the_usage_is_described(cases):
    failures = _silent_errors(cases, lambda e: _USAGE_BY_DOC_TYPE.get(e.doc_type))
    assert not failures, "\n  ".join(failures)


def test_never_wrong_in_silence_when_the_usage_was_never_described(cases):
    failures = _silent_errors(cases, lambda e: None)
    assert not failures, "\n  ".join(failures)


def test_cards_come_out_right_unless_described_as_the_opposite(cases):
    """The founder's condition from July was that an Amex file must come out
    right whatever the account was declared to be. It still holds for a card
    described as one people pay with and for an account never described. A
    card described as an account money comes in and goes out of is left as
    written, and asks: one question, never a silent error."""
    for usage in ("payments", None):
        rows = _outcomes(cases, lambda e: usage)
        wrong = [e.filename for e, right, *_ in rows if e.doc_type == "credit_card" and not right]
        assert not wrong, (usage, wrong)
    rows = _outcomes(cases, lambda e: "in_and_out")
    silent = [e.filename for e, right, asks, *_ in rows
              if e.doc_type == "credit_card" and not right and not asks]
    assert not silent, silent


def test_silence_is_the_common_case(cases):
    """Asking on every file would make the question worthless. With the usage
    described, the files the measure gets right should mostly go through."""
    rows = _outcomes(cases, lambda e: _USAGE_BY_DOC_TYPE.get(e.doc_type))
    asked = [e for e, _right, asks, *_ in rows if asks]
    assert len(asked) <= len(rows) // 2, f"{len(asked)} of {len(rows)} files ask"


def test_a_described_account_is_never_turned_the_wrong_way(cases):
    """With the usage described, the direction applied is right on every file,
    including the ones that ask: the question confirms, it does not repair."""
    rows = _outcomes(cases, lambda e: _USAGE_BY_DOC_TYPE.get(e.doc_type))
    wrong = [e.filename for e, right, *_ in rows if not right]
    assert not wrong, wrong
