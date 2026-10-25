"""Date and amount of an expense as people say or dictate them (spec 32, 4.1),
and the category found from the user's own words (spec 32, 4.3).

Pure functions: no database, no model. Synthetic text only.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.spoken_expense import (
    category_match,
    nearest_categories,
    parse_amount,
    parse_date,
    parse_expense,
    pick_category,
    pick_movement,
    words_to_int,
    yes_no,
)

TODAY = date(2026, 10, 25)      # a Sunday


# ── 4.1 The amount, as it is said ────────────────────────────────────────────

@pytest.mark.parametrize("said, value, cents", [
    ("23 e 40", "23.40", True),
    ("ventitre e quaranta", "23.40", True),
    ("23 euro e 40", "23.40", True),
    ("23 euro e 40 centesimi", "23.40", True),
    ("23 e 5", "23.05", True),           # cents said are always cents
    ("23", "23.00", False),
    ("23 euro", "23.00", False),
    ("ventitre", "23.00", False),
    ("23,40", "23.40", True),
    ("23.40", "23.40", True),
    ("40 centesimi", "0.40", True),
    ("23,4", "23.40", True),             # written decimals are decimals
    ("23,40 €", "23.40", True),
    ("€ 23,40", "23.40", True),
    ("1.234,56", "1234.56", True),
    ("23 euro 40", "23.40", True),
    ("23 virgola 40", "23.40", True),
    ("ventitré euro e quaranta centesimi", "23.40", True),
    ("centoventi", "120.00", False),
])
def test_the_amount_as_it_is_said(said, value, cents):
    amount = parse_amount(said)
    assert amount is not None, said
    assert (amount.value, amount.cents_said) == (Decimal(value), cents)


@pytest.mark.parametrize("said", ["", "ieri", "boh", "zero"])
def test_no_amount(said):
    assert parse_amount(said) is None


@pytest.mark.parametrize("word, n", [
    ("tre", 3), ("dodici", 12), ("venti", 20), ("ventuno", 21), ("ventitre", 23),
    ("ventotto", 28), ("quaranta", 40), ("novantanove", 99), ("cento", 100),
    ("centotto", 108), ("duecentoquaranta", 240), ("mille", 1000),
    ("milleduecento", 1200), ("duemila", 2000), ("casa", None), ("e", None),
])
def test_number_words(word, n):
    assert words_to_int(word) == n


# ── 4.1 The date, relative or explicit ───────────────────────────────────────

@pytest.mark.parametrize("said, day", [
    ("oggi", date(2026, 10, 25)),
    ("ieri", date(2026, 10, 24)),
    ("l'altro ieri", date(2026, 10, 23)),
    ("altroieri", date(2026, 10, 23)),
    ("sabato", date(2026, 10, 24)),
    ("sabato scorso", date(2026, 10, 24)),
    ("domenica scorsa", date(2026, 10, 18)),      # said on a Sunday: a week ago
    ("domenica", date(2026, 10, 25)),
    ("venerdì", date(2026, 10, 23)),
    ("il 12", date(2026, 10, 12)),                # current month
    ("il 28", date(2026, 9, 28)),                 # not yet come: previous month
    ("del 3", date(2026, 10, 3)),
    ("il dodici", date(2026, 10, 12)),
    ("12 maggio", date(2026, 5, 12)),
    ("il 12 di maggio", date(2026, 5, 12)),
    ("12 dicembre", date(2025, 12, 12)),          # in the future: last year
    ("1 gennaio 2026", date(2026, 1, 1)),
    ("primo ottobre", date(2026, 10, 1)),
    ("12/05/2026", date(2026, 5, 12)),
    ("12/05/26", date(2026, 5, 12)),
    ("12/05", date(2026, 5, 12)),
    ("12-05-2026", date(2026, 5, 12)),
    ("12.05.2026", date(2026, 5, 12)),
    ("yesterday", date(2026, 10, 24)),          # the example in the other languages
    ("hier", date(2026, 10, 24)),
    ("gestern", date(2026, 10, 24)),
    ("ayer", date(2026, 10, 24)),
    ("aujourd'hui", date(2026, 10, 25)),
])
def test_the_date_as_it_is_said(said, day):
    assert parse_date(said, TODAY) == day


@pytest.mark.parametrize("said", ["", "23,40", "23 e 40", "31/02/2026", "il 40"])
def test_no_date(said):
    assert parse_date(said, TODAY) is None


# ── 4.1 Both in one sentence ─────────────────────────────────────────────────

@pytest.mark.parametrize("said, day, value, cents", [
    ("ieri, 23,40", date(2026, 10, 24), "23.40", True),
    ("ieri 23 e 40", date(2026, 10, 24), "23.40", True),
    ("23 e 40 ieri", date(2026, 10, 24), "23.40", True),
    ("il 12, 23 euro", date(2026, 10, 12), "23.00", False),
    ("sabato scorso ventitre e quaranta", date(2026, 10, 24), "23.40", True),
    ("12/05 23,40", date(2026, 5, 12), "23.40", True),
    ("12 maggio, 40 centesimi", date(2026, 5, 12), "0.40", True),
    ("Oggi ho speso 7,50 €", date(2026, 10, 25), "7.50", True),
])
def test_date_and_amount_together(said, day, value, cents):
    p = parse_expense(said, TODAY)
    assert (p.day, p.amount, p.cents_said) == (day, Decimal(value), cents)
    assert p.sure


def test_one_of_the_two_missing():
    p = parse_expense("23,40", TODAY)
    assert (p.day, p.amount) == (None, Decimal("23.40"))
    p = parse_expense("ieri", TODAY)
    assert (p.day, p.amount) == (date(2026, 10, 24), None)


def test_numbers_left_over_make_it_unsure():
    """A number nobody used: the bot repeats what it understood (4.1)."""
    p = parse_expense("ieri 23 40 7", TODAY)
    assert p.amount == Decimal("23")
    assert not p.sure


# ── Answers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said, intent", [
    ("sì", "yes"), ("si", "yes"), ("Sì, è questa", "yes"), ("ok", "yes"), ("esatto", "yes"),
    ("va bene", "yes"), ("yes", "yes"),
    ("sì, ma la categoria è un'altra", "yes_other"), ("si ma è un'altra categoria", "yes_other"),
    ("no", "no"), ("No, non è questa", "no"), ("nessuna", "no"),
    ("casa", None), ("", None),
])
def test_yes_no(said, intent):
    assert yes_no(said) == intent


_CANDIDATES = [("Esselunga", date(2026, 10, 12)), ("Bar Centrale", date(2026, 10, 13))]


@pytest.mark.parametrize("said, index", [
    ("1", 0), ("la seconda", 1), ("secondo", 1), ("Esselunga", 0), ("bar", 1),
    ("il 13", 1), ("quella del 12", 0), ("l'ultima", 1), ("boh", None), ("3", None),
])
def test_choose_a_movement(said, index):
    assert pick_movement(said, _CANDIDATES, TODAY) == index


def test_choose_by_a_counterpart_shared_by_two_is_not_a_choice():
    same = [("Esselunga", date(2026, 10, 12)), ("Esselunga", date(2026, 10, 13))]
    assert pick_movement("esselunga", same, TODAY) is None
    assert pick_movement("il 13", same, TODAY) == 1


# ── 4.3 The category from the user's words ───────────────────────────────────

PAIRS = [
    ("Casa", "Manutenzione e riparazioni"), ("Casa", "Arredamento"), ("Casa", "Altro casa"),
    ("Alimentari", "Spesa supermercato"), ("Alimentari", "Macelleria / pescheria"),
    ("Salute", "Farmaci"), ("Salute", "Dentista"),
    ("Trasporti", "Carburante gasolio / benzina / GPL"), ("Trasporti", "Pedaggio"),
    ("Cura personale", "Parrucchiere / barbiere"), ("Comunicazioni", "Telefonia mobile"),
]


@pytest.mark.parametrize("said, pair", [
    ("supermercato", ("Alimentari", "Spesa supermercato")),
    ("al supermercato", ("Alimentari", "Spesa supermercato")),
    ("farmaco", ("Salute", "Farmaci")),
    ("benzina", ("Trasporti", "Carburante gasolio / benzina / GPL")),
    ("manutenzione della casa", ("Casa", "Manutenzione e riparazioni")),
    ("Salute / Dentista", ("Salute", "Dentista")),
    ("dentista", ("Salute", "Dentista")),
    ("parrucchiere", ("Cura personale", "Parrucchiere / barbiere")),
])
def test_a_direct_match(said, pair):
    assert category_match(said, PAIRS) == pair


@pytest.mark.parametrize("said", ["casa", "spesa per la casa", "boh", "salute"])
def test_no_direct_match_when_the_words_fit_several(said):
    assert category_match(said, PAIRS) is None


def test_the_three_nearest():
    near = nearest_categories("spesa per la casa", PAIRS)
    assert len(near) == 3
    assert all(c == "Casa" or s == "Spesa supermercato" for c, s in near)


def test_the_nearest_leave_out_what_was_refused():
    refused = [("Casa", "Manutenzione e riparazioni"), ("Casa", "Arredamento")]
    near = nearest_categories("casa", PAIRS, exclude=refused)
    assert not set(near) & set(refused)
    assert near[0] == ("Casa", "Altro casa")


def test_nearest_even_for_unknown_words():
    assert len(nearest_categories("xyz", PAIRS)) == 3


@pytest.mark.parametrize("said, index", [
    ("2", 1), ("il primo", 0), ("la terza", 2), ("arredamento", 1),
    ("Casa / Altro casa", 2), ("boh", None), ("4", None),
])
def test_choose_among_the_proposed_categories(said, index):
    options = PAIRS[:3]
    assert pick_category(said, options) == index
