"""Tests of the scoring in benchmark/benchmark_product_path.py.

Why these tests exist: the scoring decides which figures can be published, so a
wrong match (by position, or a movement counted twice) would silently inflate or
deflate them. Only pure functions are tested; process_file is not called.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark"))

from benchmark_product_path import ExpectedRow, declared_usage, score_file  # noqa: E402


def _expected(day: str, amount: float, description: str = "x", transfer: bool = False) -> ExpectedRow:
    """Build a ground-truth row.

    Args:
        day: ISO date.
        amount: signed amount.
        description: raw description.
        transfer: whether the row is an internal transfer.
    """
    return ExpectedRow(date=day, amount=amount, description=description, is_internal_transfer=transfer)


def test_match_ignores_position_and_sign_error_counts_only_on_signed():
    """A reordered file still matches; a flipped sign is found but not correct."""
    expected = [_expected("2026-01-01", -10.0), _expected("2026-01-02", 20.0)]
    movements = [
        {"date": date(2026, 1, 2), "amount": Decimal("20.00"), "description": "x", "tx_type": "income"},
        {"date": date(2026, 1, 1), "amount": Decimal("10.00"), "description": "x", "tx_type": "expense"},
    ]
    score = score_file(expected, movements)
    assert score.n_matched == 2
    assert score.n_amount_signed_ok == 1


def test_each_movement_used_once():
    """Two identical expected rows cannot both match a single extracted movement."""
    expected = [_expected("2026-01-01", -5.0), _expected("2026-01-01", -5.0)]
    movements = [{"date": "2026-01-01", "amount": -5.0, "description": "x"}]
    assert score_file(expected, movements).n_matched == 1


def test_description_breaks_ties():
    """With two candidates of equal date and amount, the one with the same description wins."""
    expected = [_expected("2026-01-01", -5.0, "BAR"), _expected("2026-01-01", 5.0, "RIMBORSO")]
    movements = [
        {"date": "2026-01-01", "amount": 5.0, "raw_description": "RIMBORSO"},
        {"date": "2026-01-01", "amount": -5.0, "raw_description": "BAR"},
    ]
    assert score_file(expected, movements).n_amount_signed_ok == 2


def test_transfer_recall_and_missing_rows():
    """A missing row counts as not found; a transfer typed internal counts as recalled."""
    expected = [_expected("2026-01-01", -100.0, transfer=True), _expected("2026-01-03", -1.0)]
    movements = [{"date": "2026-01-01", "amount": -100.0, "tx_type": "internal_out"}]
    score = score_file(expected, movements)
    assert (score.n_matched, score.n_transfers_expected, score.n_transfers_found) == (1, 1, 1)


def test_declared_usage():
    """Credit cards are declared as payments, every other account as in and out."""
    assert declared_usage("credit_card") == "payments"
    assert declared_usage("bank_account") == "in_and_out"
