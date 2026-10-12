"""GuidedFlowService: data for the guided workflow, computed from the DB.

Reads what the pure functions in core.guided_flow need and returns their
answers, so the UI never imports core.* (coupling rule).
"""
from __future__ import annotations

from datetime import date

from core.guided_flow import is_stale, reference_period
from services.transaction_service import TransactionService


class GuidedFlowService:
    def __init__(self, engine) -> None:
        self._tx = TransactionService(engine)

    def landing_period(self) -> tuple[date, date] | None:
        """Three months ending with the last movement, None on an empty DB."""
        last = self._tx.get_last_movement_date()
        return reference_period(last) if last else None

    def stopped_at(self, today: date | None = None) -> date | None:
        """Date of the last movement when last month is not loaded yet.

        None when the data is current or the DB is empty: the UI shows
        "you stopped at ..." only when there is something to catch up on.
        """
        last = self._tx.get_last_movement_date()
        if last is None:
            return None
        return last if is_stale(last, today or date.today()) else None
