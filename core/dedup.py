"""The same movement exported in two formats: recognise it across files.

Spec: documents/04_software_engineering/07_deterministic_pipeline.md,
"Doppioni fra file di formato diverso".

The movement id is computed on the raw values of the file, description
included, so one movement exported in two formats got two ids and entered
twice. A movement of the incoming file is a twin of one already saved when
account, date, amount and direction match, the saved one comes from another
file, and the two raw descriptions share a word of four letters or more (or
one's words open the other's). Twins are paired one to one: two identical
transfers on the same day in both files stay two.

Pure: no database, no interface.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from core.rule_key import words

_MIN_LETTERS = 4


def _direction(amount) -> str:
    return "out" if Decimal(str(amount)) < 0 else "in"


def twin_key(account: str | None, day, amount) -> tuple:
    """Account, date, amount and direction: the direction is explicit, not
    left to the sign of the amount."""
    amt = Decimal(str(amount)).quantize(Decimal("0.01"))
    return ((account or "").casefold(), str(day), abs(amt), _direction(amt))


def same_movement(description_a: str | None, description_b: str | None) -> bool:
    """A word of four letters or more in common, or one opens the other."""
    a, b = words(description_a), words(description_b)
    if not a or not b:
        return False
    shared = {w for w in a if sum(c.isalpha() for c in w) >= _MIN_LETTERS} & set(b)
    if shared:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_[: len(short)] == short


def find_twins(incoming: list[dict], saved: list[dict]) -> dict[str, dict]:
    """{incoming id: the saved movement it repeats}, paired one to one.

    *incoming*: movements of the file being imported, with ``id``,
    ``account_label``, ``date``, ``amount``, ``raw_description``.
    *saved*: movements already in the ledger from OTHER files, same keys.
    Each saved movement answers for at most one incoming movement, in order.
    """
    pool: dict[tuple, list[dict]] = defaultdict(list)
    for m in saved:
        pool[twin_key(m["account_label"], m["date"], m["amount"])].append(m)
    twins: dict[str, dict] = {}
    for tx in incoming:
        candidates = pool.get(twin_key(tx["account_label"], tx["date"], tx["amount"]))
        if not candidates:
            continue
        for i, m in enumerate(candidates):
            if same_movement(tx.get("raw_description") or tx.get("description"),
                             m.get("raw_description") or m.get("description")):
                twins[tx["id"]] = candidates.pop(i)
                break
    return twins
