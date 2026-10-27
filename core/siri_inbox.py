"""The expenses dictated to Siri, read from a text file (spec 32, 4.9).

A Shortcuts command appends one line per expense, without opening the app:

    2026-10-27 18:30 | ieri 23 e 40, caffe' al bar

The date in front is the day the user spoke: "ieri" is read against it, not
against the day the app reads the file. Each line becomes a saved receipt
(spec 32, 4.4bis) or, when the amount is missing, a sentence to complete; a
line without a day is the day the user spoke. Pure: the files are read and written by the service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from core.spoken_expense import category_match, expense_words, parse_expense

INBOX_NAME = "spese-da-siri.txt"
UNCLEAR_NAME = "spese-da-siri-non-capite.txt"
# The iPhone app writes one file per entry, named "spesa-<stamp>-<id>.txt" (spec 34, 3).
APP_ENTRY_PREFIX = "spesa-"

_PREFIX = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:[ T]\d{1,2}[:.]\d{2})?\s*\|\s*(.*)$")
# The answer to "E' in contanti?", as the command writes it; missing means card.
_PAYMENT = re.compile(r"^(rimborso-contanti|rimborso-carta|prelievo|contanti|carta|cash|card)\s*\|\s*(.*)$",
                      re.IGNORECASE)
# The field the iPhone app writes, as (kind, cash) (spec 34, 3): a withdrawal
# is cash coming in from a bank account.
_FIELD = {"contanti": ("expense", True), "cash": ("expense", True), "carta": ("expense", False),
          "card": ("expense", False), "rimborso-contanti": ("refund", True), "rimborso-carta": ("refund", False),
          "prelievo": ("withdrawal", True)}
_SAID_CASH = re.compile(r"\b(?:in\s+)?(?:contanti|cash)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SiriReceipt:
    """A dictated expense understood: what the saved receipt keeps.

    Attributes:
        day: the day of the expense, the relative words read against the day
            the user spoke.
        amount: the amount, positive, in euro.
        what: the user's own words left once date and amount are taken out;
            "" when nothing is left.
        pair: (category, subcategory) the words name, or None when they name
            none: the movement then keeps its own at the check.
        cash: True when paid in cash: never matched to a statement.
        kind: "expense", "refund" (an inflow) or "withdrawal" (cash taken from
            a bank account, a transfer) (spec 32, 4.10).
    """
    day: date
    amount: Decimal
    what: str
    pair: tuple[str, str] | None
    cash: bool = False
    kind: str = "expense"


def read_line(line: str, pairs: list[tuple[str, str]], today: date) -> SiriReceipt | str | None:
    """One line of the file: a SiriReceipt, the sentence itself when it lacks
    a date or an amount, or None for an empty line.

    Args:
        line: the line as the command wrote it, with or without the
            "AAAA-MM-GG HH:MM |" prefix and the "contanti |" or "carta |"
            field after it.
        pairs: the expense categories of the taxonomy, as (category,
            subcategory).
        today: the day used when the line has no prefix.
    """
    if not line.strip():
        return None
    said_day, text = today, line.strip()
    m = _PREFIX.match(line)
    if m:
        try:
            said_day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
        text = m.group(2).strip()
    cash, kind = False, "expense"
    p = _PAYMENT.match(text)
    if p:
        (kind, cash), text = _FIELD[p.group(1).lower()], p.group(2).strip()
    # "in contanti" said in the sentence is cash too, and not a word of what was bought.
    said_cash = _SAID_CASH.search(text)
    if said_cash:
        cash, text = True, (text[:said_cash.start()] + " " + text[said_cash.end():]).strip(" ,")
    if not text:
        return None
    parsed = parse_expense(text, said_day)
    if parsed.amount is None or parsed.amount <= 0:
        return text
    # No day said: the day the user spoke, which is when one says "12 euro
    # pizza" or "prelievo 50 euro" (founder, 2026-10-27).
    day = parsed.day or said_day
    what = expense_words(text, said_day)
    # A refund and a withdrawal are no expense: their category is not an expense one.
    pair = category_match(what, pairs) if what and kind == "expense" else None
    return SiriReceipt(day=day, amount=parsed.amount, what=what, pair=pair, cash=cash, kind=kind)
