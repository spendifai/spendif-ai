"""Pair the two sides of a transfer between the user's own accounts.

The two sides sit on different accounts, so they arrive in different files and
often in different imports: pairing inside one file never meets them. Here a
movement is compared with the stored movements of the other accounts.

Same amount with opposite sign, different accounts, dates a few days apart.
How sure a pair is depends on what the import already said about each side:

- both sides already read as transfers: ``high``, the pair is a transfer;
- one side read as transfer, the other as spending or income: ``medium``,
  proposed to the user;
- neither side, but one day apart at most: ``low``, proposed to the user.

Plain rules, no model: the import's reading is the evidence, the pairing only
puts the two halves together.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from core.models import Confidence, TransactionType

OUT_TYPES = {TransactionType.internal_out.value}
IN_TYPES = {TransactionType.internal_in.value}
# Card purchases are never one side of a transfer.
NEVER_PAIRED = {TransactionType.card_tx.value}

_RANK = {Confidence.high.value: 0, Confidence.medium.value: 1, Confidence.low.value: 2}


@dataclass(frozen=True)
class Movement:
    id: str
    date: date
    amount: Decimal
    account: str
    tx_type: str


@dataclass(frozen=True)
class TransferPair:
    out_id: str
    in_id: str
    confidence: str
    days: int              # days between the two sides
    out_marked: bool       # the outgoing side was already read as a transfer
    in_marked: bool        # the incoming side was already read as a transfer


def _cents(amount: Decimal) -> int:
    return int((abs(amount) * 100).to_integral_value())


def pair_across_accounts(
    movements: list[Movement],
    new_ids: set[str] | None = None,
    rejected: set[tuple[str, str]] | None = None,
    epsilon: Decimal = Decimal("0.01"),
    delta_days: int = 5,
    strict_days: int = 1,
) -> list[TransferPair]:
    """Pairs among ``movements``, each movement in one pair at most.

    With ``new_ids`` at least one side of every pair is a new movement (the
    import just done); without it every movement counts (first run on an
    existing ledger). Pairs in ``rejected`` (out_id, in_id) were turned down
    by the user and are never proposed again. The surest and closest pairs are
    taken first.
    """
    rejected = rejected or set()
    outs = [m for m in movements if m.amount < 0 and m.tx_type not in NEVER_PAIRED]
    ins_by_cents: dict[int, list[Movement]] = {}
    for m in movements:
        if m.amount > 0 and m.tx_type not in NEVER_PAIRED:
            ins_by_cents.setdefault(_cents(m.amount), []).append(m)

    tol = _cents(epsilon)
    candidates: list[TransferPair] = []
    for o in outs:
        c = _cents(o.amount)
        for cents in range(c - tol, c + tol + 1):
            for i in ins_by_cents.get(cents, ()):
                if i.account == o.account or (o.id, i.id) in rejected:
                    continue
                if new_ids is not None and o.id not in new_ids and i.id not in new_ids:
                    continue
                if abs(o.amount + i.amount) > epsilon:
                    continue
                days = abs((i.date - o.date).days)
                if days > delta_days:
                    continue
                out_marked = o.tx_type in OUT_TYPES
                in_marked = i.tx_type in IN_TYPES
                if out_marked and in_marked:
                    conf = Confidence.high.value
                elif out_marked or in_marked:
                    conf = Confidence.medium.value
                elif days <= strict_days:
                    conf = Confidence.low.value
                else:
                    continue
                candidates.append(TransferPair(o.id, i.id, conf, days, out_marked, in_marked))

    candidates.sort(key=lambda p: (_RANK[p.confidence], p.days, p.out_id, p.in_id))
    used: set[str] = set()
    pairs: list[TransferPair] = []
    for p in candidates:
        if p.out_id in used or p.in_id in used:
            continue
        used.update((p.out_id, p.in_id))
        pairs.append(p)
    return pairs


# An IBAN written in one piece: country, check digits, 11-30 characters. The
# spaced form is left to the description: its groups cannot be told apart from
# the words that follow.
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{11,30})\b")


def destination_key(description: str, raw_description: str = "") -> str:
    """Who a one-sided transfer went to (or came from), as a stable key.

    The IBAN when the bank's text carries one, since a name can be written in
    many ways; otherwise the cleaned description, upper case and single
    spaces. The amount is left out: transfers to the same person change amount.
    """
    text = " ".join((description or "").upper().split())
    m = _IBAN_RE.search((raw_description or "").upper()) or _IBAN_RE.search(text)
    return m.group(1) if m else text
