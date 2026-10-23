"""Transfers between the user's own accounts: the two sides put together
across imports, since each side arrives with the file of its own account."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from core.guided_flow import check_window_start
from core.models import Confidence
from core.transfer_pairing import (
    Movement,
    TransferPair,
    destination_key,
    pair_across_accounts,
)
from db import repository
from db.models import Transaction

# Days between the two sides of a transfer: a bank transfer can take a few
# working days, a weekend in between included.
PAIR_DELTA_DAYS = 5

_PAIRED_ONCE_KEY = "transfers_paired_across_imports"

# Answers for a one-sided transfer, per beneficiary.
UNLOADED = "unloaded"           # an account of mine, not loaded yet
UNTRACKED = "untracked"         # an account of mine I do not follow
OTHER_PERSON = "other_person"   # not a transfer: spending or income
DESTINATION_ANSWERS = (UNLOADED, UNTRACKED, OTHER_PERSON)


@dataclass(frozen=True)
class PairRow:
    """A pair of movements on two accounts, as the workflow step shows it."""
    out_id: str
    in_id: str
    out_date: date
    out_account: str
    in_date: date
    in_account: str
    amount: Decimal               # without sign
    confidence: str
    days: int
    out_marked: bool              # read as a transfer by the import
    in_marked: bool
    decision: str | None          # "yes", or None while unanswered


@dataclass(frozen=True)
class SingleRow:
    """A movement read as a transfer whose other side was not found."""
    tx_id: str
    date: date
    account: str
    amount: Decimal               # with sign: negative went out
    key: str                      # beneficiary, see destination_key
    description: str
    answer: str | None


@dataclass(frozen=True)
class TransferView:
    """Only what still needs an answer: a sure pair or one already answered
    is never shown again, only counted in ``settled``."""
    pairs: list[PairRow]          # proposed pairs, least sure first
    singles: list[SingleRow]      # one-sided transfers without an answer
    settled: int                  # sure or answered pairs and answered single sides
    older: int                    # open items before the window, only counted

    @property
    def open_count(self) -> int:
        return len(self.pairs) + len(self.singles)


def _movement(tx) -> Movement:
    return Movement(
        id=tx.id,
        date=date.fromisoformat(str(tx.date)[:10]),
        amount=Decimal(str(tx.amount or 0)),
        account=tx.account_label or "",
        tx_type=tx.tx_type or "unknown",
    )


class TransferService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    def pair_new(self, new_ids: set[str]) -> list[TransferPair]:
        """Pair the movements just imported with those already stored."""
        if not new_ids:
            return []
        with self._session() as s:
            new = [_movement(t) for t in repository.get_unpaired_movements(s, ids=new_ids)]
            if not new:
                return []
            lo = min(m.date for m in new) - timedelta(days=PAIR_DELTA_DAYS)
            hi = max(m.date for m in new) + timedelta(days=PAIR_DELTA_DAYS)
            txs = repository.get_unpaired_movements(s, lo.isoformat(), hi.isoformat())
            return self._apply(s, txs, new_ids)

    def pair_all(self) -> list[TransferPair]:
        """Pair every stored movement: the first run on an existing ledger."""
        with self._session() as s:
            return self._apply(s, repository.get_unpaired_movements(s), None)

    def pair_all_once(self) -> list[TransferPair]:
        """``pair_all`` the first time only; later imports pair their own."""
        with self._session() as s:
            if repository.get_user_setting(s, _PAIRED_ONCE_KEY) == "true":
                return []
        pairs = self.pair_all()
        with self._session() as s:
            repository.set_user_setting(s, _PAIRED_ONCE_KEY, "true")
            s.commit()
        return pairs

    @staticmethod
    def _apply(s, txs, new_ids: set[str] | None) -> list[TransferPair]:
        by_id = {t.id: t for t in txs}
        pairs = pair_across_accounts(
            [_movement(t) for t in txs], new_ids=new_ids,
            rejected=repository.get_rejected_transfer_pairs(s), delta_days=PAIR_DELTA_DAYS)
        for p in pairs:
            repository.set_transfer_pair(
                s, by_id[p.out_id], by_id[p.in_id], p.confidence,
                make_internal=p.confidence == Confidence.high.value,
            )
        if pairs:
            s.commit()
        return pairs

    # ── The workflow step ────────────────────────────────────────────────────

    def view(self, today: date | None = None) -> TransferView:
        """Pairs and one-sided transfers of the window the workflow asks about;
        what is older is only counted."""
        start = check_window_start(today or date.today())
        sure = Confidence.high.value
        with self._session() as s:
            pairs: list[PairRow] = []
            older = settled = 0
            for link in repository.get_open_transfer_links(s):
                o, i = s.get(Transaction, link.out_id), s.get(Transaction, link.in_id)
                if o is None or i is None:
                    continue
                mo, mi = _movement(o), _movement(i)
                done = link.user_decision is not None or link.confidence == sure
                if max(mo.date, mi.date) < start:
                    older += int(not done)
                    continue
                if done:
                    settled += 1
                    continue
                pairs.append(PairRow(
                    out_id=o.id, in_id=i.id, out_date=mo.date, out_account=mo.account,
                    in_date=mi.date, in_account=mi.account, amount=abs(mo.amount),
                    confidence=link.confidence or Confidence.medium.value,
                    days=abs((mi.date - mo.date).days),
                    out_marked=o.tx_type == "internal_out", in_marked=i.tx_type == "internal_in",
                    decision=link.user_decision,
                ))
            answers = repository.get_transfer_destinations(s)
            singles: list[SingleRow] = []
            for t in repository.get_one_sided_transfers(s):
                m = _movement(t)
                key = destination_key(t.description or "", t.raw_description or "")
                if m.date < start:
                    older += int(key not in answers)
                    continue
                if key in answers:
                    settled += 1
                    continue
                singles.append(SingleRow(
                    tx_id=t.id, date=m.date, account=m.account, amount=m.amount, key=key,
                    description=t.description or "", answer=answers.get(key)))
        rank = {Confidence.low.value: 0, Confidence.medium.value: 1}
        pairs.sort(key=lambda p: (rank.get(p.confidence, 1), p.out_date))
        singles.sort(key=lambda x: x.date)
        return TransferView(pairs=pairs, singles=singles, settled=settled, older=older)

    def decide(self, out_id: str, in_id: str, decision: str) -> None:
        """"yes": the pair is a transfer; "no": undone, never proposed again."""
        if decision not in ("yes", "no"):
            raise ValueError(f"unknown decision: {decision}")
        with self._session() as s:
            repository.decide_transfer_pair(s, out_id, in_id, decision)
            s.commit()

    def answer(self, key: str, answer: str) -> int:
        """Where the transfers to (or from) ``key`` go, once for all of them.
        "other_person" turns them into spending or income, now and in the
        imports to come. Returns the movements changed."""
        if answer not in DESTINATION_ANSWERS:
            raise ValueError(f"unknown answer: {answer}")
        with self._session() as s:
            repository.set_transfer_destination(s, key, answer)
            s.commit()
        return self._apply_destinations(None)

    def apply_destinations(self, new_ids: set[str]) -> int:
        """The answers already given, on the movements just imported."""
        return self._apply_destinations(new_ids) if new_ids else 0

    def _apply_destinations(self, ids: set[str] | None) -> int:
        changed = 0
        with self._session() as s:
            answers = repository.get_transfer_destinations(s)
            others = {k for k, a in answers.items() if a == OTHER_PERSON}
            if not others:
                return 0
            for t in repository.get_one_sided_transfers(s):
                if ids is not None and t.id not in ids:
                    continue
                if destination_key(t.description or "", t.raw_description or "") in others:
                    repository.leave_giroconto(s, t)
                    changed += 1
            if changed:
                s.commit()
        return changed
