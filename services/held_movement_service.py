"""Movements set aside at import, waiting for the user to fix them.

Today the only reason is a date after the day of the import. The movement is
kept in full and enters the ledger, as it was imported, once its date is
corrected; or it is discarded.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from core.guided_flow import swapped_date
from db import repository


@dataclass(frozen=True)
class HeldRow:
    id: str
    date: date                    # as read from the file
    account: str
    amount: Decimal
    description: str
    source_file: str
    suggested: date | None        # day and month swapped, when that makes sense


class HeldMovementService:
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

    def hold_existing(self, today: date | None = None) -> int:
        """Set aside the movements already in the ledger with a future date,
        imported before this check existed."""
        with self._session() as s:
            n = repository.hold_future_transactions(s, (today or date.today()).isoformat())
            if n:
                s.commit()
            return n

    def held(self, today: date | None = None) -> list[HeldRow]:
        today = today or date.today()
        with self._session() as s:
            rows = []
            for h in repository.get_held_movements(s):
                d = date.fromisoformat(h.date)
                rows.append(HeldRow(
                    id=h.id, date=d, account=h.account_label or "",
                    amount=Decimal(str(h.amount or 0)), description=h.description or "",
                    source_file=h.source_file or "", suggested=swapped_date(d, today)))
            return rows

    def release(self, held_id: str, new_date: date, today: date | None = None) -> bool:
        """Import the movement with ``new_date``. False when the same movement
        is already in the ledger. A date still in the future is refused."""
        if new_date > (today or date.today()):
            raise ValueError("the corrected date is still in the future")
        with self._session() as s:
            inserted = repository.release_held_movement(s, held_id, new_date.isoformat())
            s.commit()
        if inserted:
            from services.transfer_service import TransferService
            TransferService(self.engine).pair_new({held_id})
        return inserted

    def discard(self, held_id: str) -> None:
        with self._session() as s:
            repository.discard_held_movement(s, held_id)
            s.commit()
