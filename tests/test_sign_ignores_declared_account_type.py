"""What the person called the account must not decide the sign of their money.

WHY IT EXISTS
    Importing an American Express file produced expenses recorded as income.
    One cause was found and fixed in July (the header offset was not
    remembered, so the second import read the file crooked). This is the other
    one: the account type declared when the account was created was handed to
    the classifier, where it replaced the model's reading of the document and
    could invert every amount on its own.

    "Conto corrente" or "carta" is a banking distinction and the answer often
    has nothing to do with how a bank writes its files. A label chosen months
    earlier in a form is not evidence.

    The accepted condition, in the founder's words: an Amex file must come out
    right whatever the account was declared to be.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from core.classifier import _apply_doc_type_sign_prior

ACCOUNT_TYPES = (
    "bank_account",
    "credit_card",
    "debit_card",
    "prepaid_card",
    "savings_account",
    "cash",
)

ORCHESTRATOR = Path(__file__).resolve().parent.parent / "core" / "orchestrator.py"
CLASSIFIER = Path(__file__).resolve().parent.parent / "core" / "classifier.py"


# ── The wiring is gone, not merely unused ────────────────────────────────────

def test_the_import_path_never_reads_the_declared_account_type():
    """The lookup that fetched it from the Account row must stay gone.

    Written against the source rather than against behaviour on purpose: the
    defect was not a wrong value, it was the existence of a path. A test that
    only checked the outcome would pass again the day somebody re-wires the
    lookup and the value happens to agree.
    """
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "account_type"
    ]
    assert not reads, (
        "core/orchestrator.py reads an account_type attribute again: the "
        "declared type is back on the path to the sign decision"
    )


@pytest.mark.parametrize("function", ["_apply_doc_type_sign_prior", "_apply_step0_invert_sign"])
def test_neither_sign_decision_accepts_a_declared_type(function):
    tree = ast.parse(CLASSIFIER.read_text(encoding="utf-8"))
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == function
    )
    names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
    assert "account_type" not in names, f"{function} takes a declared account type again"


# ── The Amex case itself ─────────────────────────────────────────────────────

def _amex_shaped(amounts: list[float], doc_type: str) -> dict:
    """An Amex statement: a single amount column, charges written positive."""
    df = pd.DataFrame({"Importo": [str(a) for a in amounts]})
    result = {
        "amount_col": "Importo",
        "debit_col": None,
        "credit_col": None,
        "doc_type": doc_type,
        "sign_convention": "debit_positive",
        "invert_sign": False,
    }
    return _apply_doc_type_sign_prior(result, df, "amex.csv")


AMEX_CHARGES_POSITIVE = [50.0, 30.0, 1000.0, -80.0]


def test_an_amex_file_read_as_a_card_is_corrected():
    out = _amex_shaped(AMEX_CHARGES_POSITIVE, "credit_card")

    assert out["invert_sign"] is True, "charges stored positive were left as income"
    assert out["sign_convention"] == "signed_single"


def test_the_verdict_depends_on_the_document_and_on_nothing_else():
    """Six declared types, one file, one answer.

    The declared type cannot reach this decision any more, so the six cases
    are the same call. That is the point: the assertion is that the answer
    cannot be moved from outside the file.
    """
    verdicts = {
        declared: _amex_shaped(AMEX_CHARGES_POSITIVE, "credit_card")["invert_sign"]
        for declared in ACCOUNT_TYPES
    }

    assert set(verdicts.values()) == {True}, f"the answer moved with the label: {verdicts}"


def test_a_file_that_already_writes_expenses_negative_is_left_alone():
    """The mirror case, and the one an over-eager correction would break."""
    out = _amex_shaped([-97.0, -50.0, 2038.0], "credit_card")

    assert out["invert_sign"] is False
