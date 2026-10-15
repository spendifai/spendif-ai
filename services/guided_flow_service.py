"""GuidedFlowService: data for the guided workflow, computed from the DB.

Reads what the pure functions in core.guided_flow need and returns their
answers, so the UI never imports core.* (coupling rule).
"""
from __future__ import annotations

from datetime import date

from core.guided_flow import (
    CHECK_WINDOW_MONTHS,
    STEPS,
    CheckProgress,
    ClosedAccount,
    Guidance,
    GuidanceKind,
    check_progress,
    checklist_guidance,
    closed_accounts,
    group_by_account,
    is_stale,
    old_holes,
    reference_period,
    resume_step,
    suggested_closing_date,
    without_future,
)
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService

__all__ = [
    "CHECK_WINDOW_MONTHS", "STEPS", "CheckProgress", "ClosedAccount", "Guidance", "GuidanceKind", "GuidedFlowService",
    "group_by_account",
]

# Saved in the DB, not in the session: the workflow resumes after a restart.
_STEP_SETTING = "guided_flow_step"


class GuidedFlowService:
    def __init__(self, engine) -> None:
        self._engine = engine
        self._tx = TransactionService(engine)
        self._cfg = SettingsService(engine)
        self._rules = RuleService(engine)

    def checklist_guidance(self, today: date | None = None) -> list[Guidance]:
        """What to download next, most urgent first (never empty).

        Declared accounts come first in their own order; labels found only
        in the movements are appended, as the Check List grid does.
        """
        accounts = [a.name for a in self._cfg.get_accounts()]
        return checklist_guidance(accounts, self._loaded_months(), today or date.today(),
                                  self._closed())

    def format_owner(self, raw_bytes: bytes, filename: str) -> str | None:
        """The account this file's format was last imported into, if any."""
        from services.import_service import ImportService
        schema = ImportService(self._engine).find_schema_by_header(raw_bytes, filename)
        return getattr(schema, "account_label", None) or None if schema else None

    def old_holes(self, today: date | None = None) -> dict[str, int]:
        """Holes older than the window, per account: counted, not asked for."""
        accounts = [a.name for a in self._cfg.get_accounts()]
        return old_holes(accounts, self._loaded_months(), today or date.today(), self._closed())

    def closed_accounts(self) -> list[ClosedAccount]:
        return closed_accounts(self._closed(), self._loaded_months())

    def suggested_closing_date(self, account: str, today: date | None = None) -> date:
        return suggested_closing_date(self._loaded_months().get(account, set()),
                                      today or date.today())

    def close_account(self, account: str, closed_on: date) -> None:
        self._cfg.close_account(account, closed_on.isoformat())

    def reopen_account(self, account: str) -> None:
        self._cfg.reopen_account(account)

    def _loaded_months(self) -> dict[str, set[str]]:
        """Months with movements per account, the ones after the current
        month left out: see ``without_future``."""
        loaded: dict[str, set[str]] = {}
        for row in self._tx.get_monthly_tx_counts():
            if row.year_month and row.account_label and row.tx_count:
                loaded.setdefault(row.account_label, set()).add(row.year_month)
        return without_future(loaded, date.today())

    def _closed(self) -> dict[str, date]:
        return {a.name: date.fromisoformat(a.closed_on)
                for a in self._cfg.get_accounts() if getattr(a, "closed_on", None)}

    def add_account(self, name: str) -> None:
        """Register an account by name only: the bank comes with the file."""
        self._cfg.create_account(name.strip(), "")

    def closed_steps(self, today: date | None = None) -> set[str]:
        """Steps the data has already closed.

        Nothing to load means both the Check List and the import are done;
        the counterparts close once the spending checked reaches the goal,
        the mixed categories once none is left to look at, the transfers
        once every proposed pair and one-sided transfer has an answer, the
        income once every income counterpart of the period has been seen.
        The report closes only when the user moves on.
        """
        closed: set[str] = set()
        if self.checklist_guidance(today)[0].kind is GuidanceKind.up_to_date:
            closed |= {"checklist", "import"}
        if self.check_progress().reached:
            closed.add("counterparts")
        if not self.mixed_counterparts():
            closed.add("mixed")
        from services.transfer_service import TransferService
        if TransferService(self._engine).view(today).open_count == 0:
            closed.add("transfers")
        if not self.unchecked_income():
            closed.add("income")
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

    # ── Income step ──────────────────────────────────────────────────────────

    def income_counterparts(self) -> list[dict]:
        """Income counterparts of the landing period, largest total first.

        ``also_spending`` marks a counterpart that has spending movements
        too (a shop that refunded a purchase, a payment service): a rule on
        its description would change those as well, so its income movements
        are categorized one by one instead.
        """
        period = self.landing_period()
        if period is None:
            return []
        stats = self._tx.get_counterpart_stats(
            tx_types=("income",),
            date_from=period[0].isoformat(),
            date_to=period[1].isoformat(),
        )
        spending = {
            s["description"].upper() for s in self._tx.get_counterpart_stats(tx_types=("expense",))
        }
        for s in stats:
            s["also_spending"] = s["description"].upper() in spending
        return sorted(stats, key=lambda s: -s["total_amount"])

    def unchecked_income(self) -> list[dict]:
        """Income counterparts of the period the user has not seen yet."""
        return [s for s in self.income_counterparts() if not s["human_checked"]]

    def check_progress(self) -> CheckProgress:
        """Spending of the landing period checked so far, against the goal."""
        return check_progress([
            (s["total_amount"], s["human_checked"]) for s in self.expense_counterparts()
        ])

    def rule_categorized_spending(self) -> tuple[int, float]:
        """(movements, total) of the period's spending the rules already took care of."""
        period = self.landing_period()
        if period is None:
            return 0, 0.0
        return self._tx.get_rule_categorized_spending(period[0].isoformat(), period[1].isoformat())

    # ── Mixed categories step ────────────────────────────────────────────────

    def mixed_counterparts(self) -> list[dict]:
        """Spending counterparts with movements in more than one category.

        Only those no rule covers (a rule gives every movement the same
        category) and with movements the user has not seen yet: a
        counterpart already worked through comes back only with what the
        last import added. Largest total first.
        """
        rules = self._rules.get_rules()
        return [
            s for s in self.expense_counterparts()
            if len(s["categories"]) > 1
            and s["unchecked_count"] > 0
            and not any(r.matches(s["description"]) for r in rules)
        ]

    def unchecked_movements(self, description: str) -> list:
        """Movements of the counterpart in the period that the user has not seen."""
        period = self.landing_period()
        if period is None:
            return []
        return self._tx.get_counterpart_movements(
            description, period[0].isoformat(), period[1].isoformat(),
        )
