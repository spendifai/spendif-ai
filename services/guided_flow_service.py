"""GuidedFlowService: data for the guided workflow, computed from the DB.

Reads what the pure functions in core.guided_flow need and returns their
answers, so the UI never imports core.* (coupling rule).
"""
from __future__ import annotations

from datetime import date

from core.guided_flow import (
    STEPS,
    CheckProgress,
    Guidance,
    GuidanceKind,
    check_progress,
    checklist_guidance,
    is_stale,
    reference_period,
    resume_step,
)
from services.settings_service import SettingsService
from services.transaction_service import TransactionService

__all__ = ["STEPS", "CheckProgress", "Guidance", "GuidanceKind", "GuidedFlowService"]

# Saved in the DB, not in the session: the workflow resumes after a restart.
_STEP_SETTING = "guided_flow_step"


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

    def closed_steps(self, today: date | None = None) -> set[str]:
        """Steps the data has already closed.

        Nothing to load means both the Check List and the import are done;
        the counterparts close once the spending checked reaches the goal.
        Rules and report close only when the user moves on.
        """
        closed: set[str] = set()
        if self.checklist_guidance(today)[0].kind is GuidanceKind.up_to_date:
            closed |= {"checklist", "import"}
        if self.check_progress().reached:
            closed.add("counterparts")
        return closed

    def resume(self, today: date | None = None) -> str:
        """The step to open when the user comes back to the workflow."""
        return resume_step(STEPS, self.saved_step(), self.closed_steps(today))

    def saved_step(self) -> str | None:
        return self._cfg.get(_STEP_SETTING)

    def save_step(self, step: str) -> None:
        if step not in STEPS:
            raise ValueError(f"unknown step: {step}")
        self._cfg.set(_STEP_SETTING, step)

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

    # ── Counterparts step ────────────────────────────────────────────────────

    def expense_counterparts(self) -> list[dict]:
        """Spending counterparts of the landing period, largest total first.

        Expenses only: one salary would otherwise cover most of the money
        without a single expense checked. Empty on an empty DB.
        """
        period = self.landing_period()
        if period is None:
            return []
        stats = self._tx.get_counterpart_stats(
            tx_types=("expense",),
            date_from=period[0].isoformat(),
            date_to=period[1].isoformat(),
        )
        return sorted(stats, key=lambda s: -s["total_amount"])

    def check_progress(self) -> CheckProgress:
        """Spending of the landing period checked so far, against the goal."""
        return check_progress([
            (s["total_amount"], s["human_checked"]) for s in self.expense_counterparts()
        ])
