"""The expenses dictated to Siri, read from a text file (spec 32, 4.9).

A Shortcuts command appends one line per expense, without opening the app:

    2026-10-27 18:30 | ieri 23 e 40, caffe' al bar

The date in front is the day the user spoke: "ieri" is read against it, not
against the day the app reads the file. Each line becomes a saved receipt
(spec 32, 4.4bis) or, when date or amount are missing, a sentence to
complete. Pure: the files are read and written by the service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from core.spoken_expense import category_match, expense_words, parse_expense

INBOX_NAME = "spese-da-siri.txt"
UNCLEAR_NAME = "spese-da-siri-non-capite.txt"

_PREFIX = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:[ T]\d{1,2}[:.]\d{2})?\s*\|\s*(.*)$")
# The answer to "E' in contanti?", as the command writes it; missing means card.
_PAYMENT = re.compile(r"^(contanti|carta|cash|card)\s*\|\s*(.*)$", re.IGNORECASE)


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
    """
    day: date
    amount: Decimal
    what: str
    pair: tuple[str, str] | None
    cash: bool = False


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
    cash = False
    p = _PAYMENT.match(text)
    if p:
        cash, text = p.group(1).lower() in ("contanti", "cash"), p.group(2).strip()
    if not text:
        return None
    parsed = parse_expense(text, said_day)
    if parsed.day is None or parsed.amount is None or parsed.amount <= 0:
        return text
    what = expense_words(text, said_day)
    return SiriReceipt(day=parsed.day, amount=parsed.amount, what=what,
                       pair=category_match(what, pairs) if what else None, cash=cash)
