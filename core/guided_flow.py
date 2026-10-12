"""Guided workflow: what the user should load next, and where to land.

Pure functions, no database and no Streamlit. The UI turns the results
into sentences; this module only decides *what* to say, so every branch
is testable with fixed dates.

Two questions are answered here:

  1. Check List guidance: for each account, is something missing, and
     which exact date range should the user download from the bank?
  2. Reference period: which three months to show when the user lands on
     the report or reopens the app. Anchored to the last imported
     movement, never to today, so a user who has not imported for months
     sees where they left off instead of an empty chart.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from enum import Enum


class GuidanceKind(str, Enum):
    no_accounts = "no_accounts"            # onboarding registered no account
    nothing_imported = "nothing_imported"  # accounts exist, no movement at all
    never_loaded = "never_loaded"          # this account has no movement, others do
    behind = "behind"                      # last loaded month is before last month
    gap = "gap"                            # empty months between loaded ones
    up_to_date = "up_to_date"              # every account covers last month


@dataclass(frozen=True)
class Guidance:
    """One suggestion: download `account` from `date_from` to `date_to`.

    `last_loaded` is the last month with movements for that account
    (first day of the month), used for "loaded up to August 2026".
    For `no_accounts` and `up_to_date` the account and dates are None,
    except `up_to_date.last_loaded`, the month everything is loaded to.
    """
    kind: GuidanceKind
    account: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    last_loaded: date | None = None


# ── Month arithmetic ─────────────────────────────────────────────────────────

def _ym(d: date) -> tuple[int, int]:
    return d.year, d.month


def _shift(ym: tuple[int, int], months: int) -> tuple[int, int]:
    idx = ym[0] * 12 + (ym[1] - 1) + months
    return idx // 12, idx % 12 + 1


def _first_day(ym: tuple[int, int]) -> date:
    return date(ym[0], ym[1], 1)


def _last_day(ym: tuple[int, int]) -> date:
    return date(ym[0], ym[1], calendar.monthrange(ym[0], ym[1])[1])


def _parse_ym(s: str) -> tuple[int, int]:
    """'2026-08' -> (2026, 8)."""
    return int(s[:4]), int(s[5:7])


def _months_between(first: tuple[int, int], last: tuple[int, int]) -> int:
    return (last[0] * 12 + last[1]) - (first[0] * 12 + first[1])


# ── Check List guidance ──────────────────────────────────────────────────────

def checklist_guidance(
    accounts: list[str],
    loaded_months: dict[str, set[str]],
    today: date,
) -> list[Guidance]:
    """What the user should download next, most urgent first.

    Args:
        accounts: every account the user has, in display order. Accounts
            that only appear in `loaded_months` are added at the end.
        loaded_months: account -> set of 'YYYY-MM' months that have at
            least one movement (the Check List pivot, without counts).
        today: injected for testability.

    The first item is the one the UI puts in evidence with the button;
    the others are listed below it. The list is never empty: when
    nothing is missing it holds a single `up_to_date` item.

    "Last month" is the previous calendar month: the current one is
    still running and no bank has closed its statement yet. The first
    round (nothing imported at all) suggests that month only, for every
    account, so the first pass through the workflow stays small.
    """
    all_accounts = list(accounts) + sorted(
        a for a in loaded_months if a not in set(accounts)
    )
    if not all_accounts:
        return [Guidance(GuidanceKind.no_accounts)]

    last_month = _shift(_ym(today), -1)
    one_month = (_first_day(last_month), _last_day(last_month))

    if not any(loaded_months.get(a) for a in all_accounts):
        return [Guidance(GuidanceKind.nothing_imported, a, *one_month) for a in all_accounts]

    never: list[Guidance] = []
    behind: list[tuple[int, Guidance]] = []
    gaps: list[Guidance] = []

    for acc in all_accounts:
        months = sorted(_parse_ym(m) for m in loaded_months.get(acc, ()))
        if not months:
            never.append(Guidance(GuidanceKind.never_loaded, acc, *one_month))
            continue

        first, last = months[0], months[-1]
        if last < last_month:
            behind.append((
                _months_between(last, last_month),
                Guidance(
                    GuidanceKind.behind, acc,
                    date_from=_first_day(_shift(last, 1)),
                    date_to=today,
                    last_loaded=_first_day(last),
                ),
            ))

        present = set(months)
        run_start: tuple[int, int] | None = None
        ym = first
        while ym <= last:
            if ym not in present and run_start is None:
                run_start = ym
            if ym in present and run_start is not None:
                gaps.append(Guidance(
                    GuidanceKind.gap, acc,
                    date_from=_first_day(run_start),
                    date_to=_last_day(_shift(ym, -1)),
                    last_loaded=_first_day(last),
                ))
                run_start = None
            ym = _shift(ym, 1)

    # Most behind first: an account never loaded is behind by definition,
    # then by months missing (stable on ties, so display order wins),
    # then holes in the middle, oldest first.
    behind.sort(key=lambda item: -item[0])
    gaps.sort(key=lambda g: g.date_from)
    result = never + [g for _, g in behind] + gaps

    if not result:
        last_loaded = max(
            _parse_ym(m) for a in all_accounts for m in loaded_months.get(a, ())
        )
        return [Guidance(GuidanceKind.up_to_date, last_loaded=_first_day(last_loaded))]
    return result


# ── Reference period ─────────────────────────────────────────────────────────

def reference_period(last_movement: date) -> tuple[date, date]:
    """The three months ending with the month of the last movement.

    Last movement on 14 Aug 2026 -> 1 Jun 2026 .. 31 Aug 2026.
    """
    end = _ym(last_movement)
    return _first_day(_shift(end, -2)), _last_day(end)


def is_stale(last_movement: date, today: date) -> bool:
    """True when the user has not loaded last month yet.

    Same threshold as the Check List: the current month never counts as
    missing, the previous one does. The UI then says "you stopped at ...".
    """
    return _ym(last_movement) < _shift(_ym(today), -1)


# ── Counterparts: how much of the spending the user has checked ─────────────

# Share of the period's spending the workflow suggests checking. Said to the
# user in words ("8 euros out of 10"), never as a percentage.
CHECK_GOAL_SHARE = 0.8


@dataclass(frozen=True)
class CheckProgress:
    """Spending checked so far against the goal, in money.

    `missing` is how much spending is still to check to reach the goal and
    `needed` how many unchecked counterparts, largest first, cover it.
    Both are 0 once the goal is reached.
    """
    total: float
    checked: float
    missing: float
    needed: int

    @property
    def reached(self) -> bool:
        return self.missing <= 0


def check_progress(
    counterparts: list[tuple[float, bool]], share: float = CHECK_GOAL_SHARE,
) -> CheckProgress:
    """Progress over (period total, checked) pairs, one per counterpart.

    Ticking the largest unchecked counterparts first reaches the goal with
    the fewest ticks, so `needed` counts them in that order.
    """
    total = sum(amount for amount, _ in counterparts)
    checked = sum(amount for amount, ok in counterparts if ok)
    goal = total * share
    missing = max(goal - checked, 0.0)
    needed = 0
    covered = checked
    for amount in sorted((a for a, ok in counterparts if not ok), reverse=True):
        if covered >= goal:
            break
        covered += amount
        needed += 1
    return CheckProgress(total=total, checked=checked, missing=missing, needed=needed)


# ── Resume ───────────────────────────────────────────────────────────────────

# Steps of the first version, in order. The transfers step ("are the money
# moves between your accounts consistent?") comes in a second phase, after
# "import"; the UI numbers only the steps listed here.
STEPS: list[str] = ["checklist", "import", "counterparts", "mixed", "report"]


def resume_step(steps: list[str], saved: str | None, closed: set[str]) -> str:
    """The step to open when the user comes back to the workflow.

    Back to the saved step, unless the data closed it in the meantime
    (e.g. counterparts verified from the regular page): then the first
    open step after it. If everything after it is closed, the cycle
    starts again from the first step.
    """
    if not steps:
        raise ValueError("steps must not be empty")
    start = steps.index(saved) if saved in steps else 0
    for step in steps[start:]:
        if step not in closed:
            return step
    return steps[0]
