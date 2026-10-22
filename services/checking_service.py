"""Checking movements one account at a time (spec 31).

For every account: the last movement the user checked, the first one not
checked yet and how many are left; the account whose oldest unchecked
movement is furthest back is proposed, starting from that movement, so
nothing left behind is ever skipped (founder, 2026-10-22). The grid then
shows the unchecked movements of that account from that day, oldest first.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from db.models import Transaction


@dataclass(frozen=True)
class AccountCheck:
    """Where the check of one account stands."""
    account: str
    last_checked: date | None     # date of the most recent checked movement
    first_unchecked: date | None  # date of the oldest unchecked movement
    to_check: int                 # unchecked movements

    @property
    def start(self) -> date | None:
        """Where the check starts: the oldest unchecked movement."""
        return self.first_unchecked


def _day(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def proposed(accounts: list[AccountCheck]) -> AccountCheck | None:
    """The account furthest behind: the oldest unchecked movement, then the
    most movements to check. None when all are even."""
    open_ = [a for a in accounts if a.to_check]
    if not open_:
        return None
    return min(open_, key=lambda a: (a.first_unchecked, -a.to_check, a.account))


class CheckingService:
    def __init__(self, engine) -> None:
        self._engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    def accounts(self) -> list[AccountCheck]:
        """Every account with movements, in name order."""
        with self._session() as s:
            checked = Transaction.human_validated.is_(True)
            rows = s.query(
                Transaction.account_label,
                func.max(Transaction.date).filter(checked),
                func.min(Transaction.date).filter(~checked | Transaction.human_validated.is_(None)),
                func.count(Transaction.id).filter(~checked | Transaction.human_validated.is_(None)),
            ).filter(Transaction.account_label.isnot(None)).group_by(Transaction.account_label).all()
            return [AccountCheck(account=account, last_checked=_day(last) if last else None,
                                 first_unchecked=_day(first) if first else None, to_check=n or 0)
                    for account, last, first, n in sorted(rows, key=lambda r: r[0])]

    def unchecked(self, account: str, since: date) -> list[Transaction]:
        """The unchecked movements of *account* from *since*, oldest first."""
        with self._session() as s:
            return (s.query(Transaction)
                    .filter(Transaction.account_label == account,
                            Transaction.human_validated.isnot(True),
                            Transaction.date >= since.isoformat())
                    .order_by(Transaction.date, Transaction.id)
                    .all())

    def remaining(self, account: str, since: date) -> int:
        with self._session() as s:
            return (s.query(func.count(Transaction.id))
                    .filter(Transaction.account_label == account,
                            Transaction.human_validated.isnot(True),
                            Transaction.date >= since.isoformat())
                    .scalar() or 0)

    def one_sided_transfers(self, account: str, since: date) -> list:
        """The account's transfers without the other side from *since* that
        still need an answer (spec 31, step 2). Card settlements are not
        transfers and never appear."""
        from services.transfer_service import TransferService
        return [x for x in TransferService(self._engine).view().singles
                if x.account == account and x.date >= since]
