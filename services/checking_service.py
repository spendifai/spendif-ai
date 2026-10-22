"""Checking movements one account at a time (spec 31).

For every account: the last movement the user checked, how many are left
after it and how many unchecked ones sit before it; the account furthest
behind is proposed with the day to start from. The grid then shows the
unchecked movements of that account from that day, oldest first.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from db.models import Transaction


@dataclass(frozen=True)
class AccountCheck:
    """Where the check of one account stands."""
    account: str
    last_checked: date | None     # date of the most recent checked movement
    first: date                   # date of the account's first movement
    to_check: int                 # unchecked movements from ``start`` on
    before: int                   # unchecked movements before ``start``

    @property
    def start(self) -> date:
        """The day after the last checked movement; the first movement when
        nothing was ever checked."""
        return self.last_checked + timedelta(days=1) if self.last_checked else self.first


def _day(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def proposed(accounts: list[AccountCheck]) -> AccountCheck | None:
    """The account furthest behind: the oldest last check (never checked
    first), then the most movements to check. None when all are even."""
    open_ = [a for a in accounts if a.to_check]
    if not open_:
        return None
    return min(open_, key=lambda a: (a.last_checked or date.min, -a.to_check, a.account))


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
            rows = s.query(
                Transaction.account_label,
                func.min(Transaction.date),
                func.max(Transaction.date).filter(Transaction.human_validated.is_(True)),
            ).filter(Transaction.account_label.isnot(None)).group_by(Transaction.account_label).all()
            result = []
            for account, first, last in sorted(rows, key=lambda r: r[0]):
                last_day = _day(last) if last else None
                start = last_day + timedelta(days=1) if last_day else _day(first)
                unchecked = s.query(Transaction.date).filter(
                    Transaction.account_label == account,
                    Transaction.human_validated.isnot(True),
                ).all()
                after = sum(1 for (d,) in unchecked if _day(d) >= start)
                result.append(AccountCheck(account=account, last_checked=last_day, first=_day(first),
                                           to_check=after, before=len(unchecked) - after))
            return result

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
