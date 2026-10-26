"""GuidedFlowService: data for the guided workflow, computed from the DB.

Reads what the pure functions in core.guided_flow need and returns their
answers, so the UI never imports core.* (coupling rule).
"""
from __future__ import annotations

from datetime import date

from core.rule_key import exact_key

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
    SMALL_SINGLES_SHARE,
    SMALL_SINGLES_YEARS,
    SmallSingles,
    small_single_limit,
    small_singles,
    suggested_closing_date,
    without_future,
)
from services.settings_service import SettingsService
from services.transaction_service import TransactionService

__all__ = [
    "CHECK_WINDOW_MONTHS", "STEPS", "CheckProgress", "ClosedAccount", "Guidance", "GuidanceKind", "GuidedFlowService",
    "group_by_account",
]

# Saved in the DB, not in the session: the workflow resumes after a restart.
_STEP_SETTING = "guided_flow_step"



def _most_movements_first(stats: dict) -> tuple:
    """Most movements first, then the largest total: a counterpart that
    repeats is decided once for every import, and it is the one that comes
    written in different ways (founder, 2026-10-20)."""
    return (-stats["tx_count"], -stats["total_amount"])

class GuidedFlowService:
    # Share of the imported spending left to the small single counterparts.
    small_singles_share = SMALL_SINGLES_SHARE
    small_singles_years = SMALL_SINGLES_YEARS

    def __init__(self, engine) -> None:
        self._engine = engine
        self._tx = TransactionService(engine)
        self._cfg = SettingsService(engine)

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
        the counterparts close once the spending categorized by the user
        reaches the goal, the mixed categories once none is left to look at,
        the transfers once every proposed pair and one-sided transfer has an
        answer, the income once every income counterpart of the period has a
        category decided by the user.
        The report closes only when the user moves on.
        """
        closed: set[str] = set()
        # An empty registry has everything still to load, whatever the Check
        # List says without accounts to look at (spec 16, 2).
        empty = not TransactionService(self._engine).has_transactions()
        if not empty and self.checklist_guidance(today)[0].kind is GuidanceKind.up_to_date:
            closed |= {"checklist", "import"}
        if self.check_progress().reached:
            closed.add("counterparts")
        if not self.mixed_counterparts():
            closed.add("mixed")
        from services.transfer_service import TransferService
        if TransferService(self._engine).view(today).open_count == 0:
            closed.add("transfers")
        if not self.uncategorized_income():
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

    def count_rows(self, files: list[tuple[str, bytes]]) -> int:
        """Rows in the files about to be imported (for "6 files, 223 rows")."""
        from services.import_service import ImportService
        return ImportService(self._engine).count_rows(files)

    def workflow_period(self) -> tuple[date, date] | None:
        """The period the steps of the workflow look at: the whole last import.

        From its oldest to its most recent movement: a first import with two
        years of history shows the counterparts of the two years, a monthly
        import the month. Without a finished import to go by, the three
        months of landing_period. Home and Report keep landing_period.
        """
        return self._tx.get_last_import_period() or self.landing_period()

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

    def expense_counterparts(self, include_small: bool = False) -> list[dict]:
        """Spending counterparts of the landing period worth asking about,
        most movements first, then the largest total.

        The counterparts with a single movement up to the threshold drawn
        from the last years' spending are left out, of the list and of the
        goal (small_singles says which), unless the user asks to see them
        (include_small; the goal stays the same).

        Spending only: one salary would otherwise cover most of the money
        without a single expense checked. Spending is the bank's expenses and
        the purchases on a card statement, whose movements are all card_tx
        (refunds, with a positive amount, stay out). Empty on an empty DB.
        """
        stats = self._period_spending()
        hidden = set() if include_small else self.small_singles(stats).descriptions
        return sorted((s for s in stats if s["description"] not in hidden),
                      key=_most_movements_first)

    def small_singles(self, stats: list[dict] | None = None) -> SmallSingles:
        """The single-movement counterparts of the period left out of the
        workflow: how many, how much, up to which amount."""
        if stats is None:
            stats = self._period_spending()
        return small_singles(stats, self.small_single_limit())

    def small_single_limit(self, today: date | None = None) -> float:
        """The threshold of the single counterparts (core.guided_flow): 5% of
        the yearly spending of the last SMALL_SINGLES_YEARS years, shared out
        among the singles from the smallest."""
        today = today or date.today()
        since = f"{today.year - SMALL_SINGLES_YEARS}-{today:%m-%d}"   # ISO dates compare as text
        movements = [(d[:4], exact_key(desc), amount)
                     for d, desc, amount in self._tx.spending_since(since)]
        return small_single_limit(movements)

    def _period_spending(self) -> list[dict]:
        """Spending counterparts of the period of the last import."""
        period = self.workflow_period()
        if period is None:
            return []
        return self._tx.get_counterpart_stats(
            tx_types=("expense", "card_tx"),
            negative_only=True,
            date_from=period[0].isoformat(),
            date_to=period[1].isoformat(),
        )

    # ── Income step ──────────────────────────────────────────────────────────

    def income_counterparts(self) -> list[dict]:
        """Income counterparts of the landing period, largest total first.

        ``also_spending`` marks a counterpart that has spending movements
        too (a shop that refunded a purchase, a payment service): a rule on
        its description would change those as well, so its income movements
        are categorized one by one instead.
        """
        period = self.workflow_period()
        if period is None:
            return []
        # Refunds on a card statement are card_tx with a positive amount.
        stats = self._tx.get_counterpart_stats(
            tx_types=("income", "card_tx"),
            positive_only=True,
            date_from=period[0].isoformat(),
            date_to=period[1].isoformat(),
        )
        spending = {
            exact_key(s["description"]) for s in self._tx.get_counterpart_stats(
                tx_types=("expense", "card_tx"), negative_only=True)
        }
        for s in stats:
            s["also_spending"] = exact_key(s["description"]) in spending
        return sorted(stats, key=_most_movements_first)

    def uncategorized_income(self) -> list[dict]:
        """Income counterparts of the period without a category decided by the user."""
        return [s for s in self.income_counterparts() if not s["categorized_by_user"]]

    def check_progress(self) -> CheckProgress:
        """Spending movements of the period categorized by the user, against the goal.

        Counted in movements, as the list is ordered: what repeats is where a
        decision saves work at every import (founder, 2026-10-20).
        """
        return check_progress([
            (s["tx_count"], s["categorized_by_user"]) for s in self.expense_counterparts()
        ])

    def rule_categorized_spending(self) -> tuple[int, float]:
        """(movements, total) of the period's spending the rules already took care of."""
        period = self.workflow_period()
        if period is None:
            return 0, 0.0
        return self._tx.get_rule_categorized_spending(period[0].isoformat(), period[1].isoformat())

    # ── Mixed categories step ────────────────────────────────────────────────

    def mixed_counterparts(self) -> list[dict]:
        """Spending counterparts with movements in more than one category.

        Only those not yet categorized by the user: no rule covers them (a
        rule gives every movement the same category) and some movements
        still carry a category the user did not decide. A counterpart
        already worked through comes back only with what the last import
        added. Most movements first, then the largest total.

        Multi-category counterparts (spec 24) come with the movements still to
        see one by one, and without the question.
        """
        mixed = [
            s for s in self.expense_counterparts()
            if len(s["categories"]) > 1 and not s["categorized_by_user"]
        ]
        # Multi-category counterparts (spec 24): only their movements still to
        # see one by one, whatever their size; never the question again.
        multi = [s for s in self._period_spending()
                 if s["is_multi_category"] and s["to_see_one_by_one"]]
        return sorted(mixed + multi, key=_most_movements_first)

    def uncategorized_movements(self, description: str) -> list:
        """Movements of the counterpart in the period whose category the user has not decided."""
        period = self.workflow_period()
        if period is None:
            return []
        return self._tx.get_counterpart_movements(
            description, period[0].isoformat(), period[1].isoformat(),
        )
