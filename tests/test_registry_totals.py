"""Registry totals leave out giroconti and card settlements, and the page says
how many of the shown movements were left out: hiding the giroconti changes
the table, never the totals."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from ui.i18n import t
from ui.registry_page import excluded_from_totals


def _tx(amount, tx_type):
    return SimpleNamespace(amount=amount, tx_type=tx_type)


def test_counts_and_sums_only_the_movements_left_out():
    txs = [
        _tx(-50.0, "expense"),
        _tx(1200.0, "income"),
        _tx(-300.0, "internal_out"),
        _tx(300.0, "internal_in"),
        _tx(-742.08, "card_settlement"),
        _tx(-20.0, "card_tx"),
    ]
    assert excluded_from_totals(txs) == (3, Decimal("1042.08"), Decimal("300.0"))


def test_nothing_left_out_when_the_giroconti_are_hidden():
    assert excluded_from_totals([_tx(-50.0, "expense"), _tx(10.0, "income")]) == (0, 0, 0)


def test_the_line_names_count_and_amounts():
    line = t("ledger.metric.excluded", n=3, out="1.042,08", inc="300,00")
    assert "3" in line and "1.042,08" in line and "300,00" in line
