"""The expenses dictated to Siri (spec 32, 4.9): one line per expense, the
date in front the day the user spoke, each line a saved receipt or a
sentence to complete."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.siri_inbox import SiriReceipt, read_line

TODAY = date(2026, 10, 30)
PAIRS = [("Alimentari", "Spesa supermercato"), ("Salute", "Farmaci"), ("Trasporti", "Carburante")]


def test_yesterday_is_read_against_the_day_spoken():
    r = read_line("2026-10-27 18:30 | ieri 23 e 40, caffè al bar", PAIRS, TODAY)
    assert r == SiriReceipt(day=date(2026, 10, 26), amount=Decimal("23.40"), what="caffe al bar", pair=None)


def test_the_words_name_the_category():
    r = read_line("2026-10-27 09:00 | oggi 12 euro farmaci", PAIRS, TODAY)
    assert (r.day, r.amount, r.pair) == (date(2026, 10, 27), Decimal("12"), ("Salute", "Farmaci"))


def test_a_line_without_prefix_reads_against_today():
    r = read_line("il secondo del mese 30 euro carburante", PAIRS, TODAY)
    assert (r.day, r.amount, r.pair) == (date(2026, 10, 2), Decimal("30"), ("Trasporti", "Carburante"))


@pytest.mark.parametrize("line", ["2026-10-27 18:30 | caffè al bar", "2026-10-27 18:30 | ieri al bar"])
def test_without_amount_or_date_the_sentence_is_kept(line):
    assert read_line(line, PAIRS, TODAY) == line.split("| ")[1]


@pytest.mark.parametrize("line", ["", "   ", "2026-10-27 18:30 | "])
def test_an_empty_line_is_nothing(line):
    assert read_line(line, PAIRS, TODAY) is None


@pytest.mark.parametrize("field, cash", [("contanti", True), ("carta", False), ("Contanti", True)])
def test_the_payment_field(field, cash):
    r = read_line(f"2026-10-27 18:30 | {field} | ieri 4 euro giornale", PAIRS, TODAY)
    assert (r.cash, r.amount, r.what) == (cash, Decimal("4"), "giornale")


def test_without_the_payment_field_it_is_card():
    assert read_line("2026-10-27 18:30 | ieri 4 euro giornale", PAIRS, TODAY).cash is False


def test_in_cash_said_in_the_sentence():
    r = read_line("2026-10-27 18:30 | carta | ieri 4 euro giornale in contanti", PAIRS, TODAY)
    assert (r.cash, r.what) == (True, "giornale")


@pytest.mark.parametrize("field, kind, cash", [
    ("rimborso-contanti", "refund", True),
    ("rimborso-carta", "refund", False),
    ("prelievo", "withdrawal", True),
    ("contanti", "expense", True),
])
def test_the_kind_field_of_the_iphone_app(field, kind, cash):
    """The iPhone app writes the kind with the payment (spec 34, 3); a refund
    and a withdrawal name no expense category."""
    got = read_line(f"2026-10-25 18:30 | {field} | 30 euro reso scarpe", [("Abbigliamento", "Scarpe")],
                    date(2026, 10, 25))
    assert (got.kind, got.cash) == (kind, cash)
    assert got.amount == Decimal("30")
    if kind != "expense":
        assert got.pair is None


def test_without_a_day_it_is_the_day_spoken():
    """"12 euro pizza" said right after paying: the day of the line."""
    got = read_line("2026-10-27 18:30 | carta | 12 euro pizza", PAIRS, TODAY)
    assert (got.day, got.amount) == (date(2026, 10, 27), Decimal("12"))
