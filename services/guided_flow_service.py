"""GuidedFlowService: data for the guided workflow, computed from the DB.

Reads what the pure functions in core.guided_flow need and returns their
answers, so the UI never imports core.* (coupling rule).
"""
from __future__ import annotations

from datetime import date

from core.guided_flow import (
    Guidance,
    GuidanceKind,
    checklist_guidance,
    is_stale,
    reference_period,
)
from services.settings_service import SettingsService
from services.transaction_service import TransactionService

__all__ = ["Guidance", "GuidanceKind", "GuidedFlowService"]


class GuidedFlowService:
    def __init__(self, engine) -> None:
        self._tx = TransactionService(engine)
        self._cfg = SettingsService(engine)

    def checklist_guidance(self, today: date | None = None) -> list[Guidance]:
        """What to download next, most urgent first (never empty).

        Declared accounts come first in their own order; labels found only
        in the movements are appended, as the Check List grid does.
        """
        accounts = [a.name for a in self._cfg.get_accounts()]
        loaded: dict[str, set[str]] = {}
        for row in self._tx.get_monthly_tx_counts():
            if row.year_month and row.account_label and row.tx_count:
                loaded.setdefault(row.account_label, set()).add(row.year_month)
        return checklist_guidance(accounts, loaded, today or date.today())

    def add_account(self, name: str) -> None:
        """Register an account by name only: the bank comes with the file."""
        self._cfg.create_account(name.strip(), "")

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
