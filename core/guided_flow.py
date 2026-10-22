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
    dormant = "dormant"                    # nothing since before the window: closed?
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


# How far back the Check List asks for data: a quarter plus a month to catch
# up with it. Someone who leaves more than three months before looking at
# where the money went is not chasing old statements, and asking for them
# turns a reminder into a backlog.
CHECK_WINDOW_MONTHS = 4


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

def without_future(loaded_months: dict[str, set[str]], today: date) -> dict[str, set[str]]:
    """Drop the months after the current one. A movement dated in the future
    (a typo in the bank's file, a misread date) is not a loaded month: counted
    as one, it would open a hole that ends in the future and ask the user for
    movements that cannot exist yet."""
    now = _ym(today)
    return {acc: {m for m in ms if _parse_ym(m) <= now} for acc, ms in loaded_months.items()}


def swapped_date(d: date, today: date) -> date | None:
    """The date with day and month swapped, when that gives a real date not
    after ``today``: the usual reading error behind a date in the future
    (02/12 read as 2 December instead of 12 February). None otherwise; the
    user confirms it either way."""
    if d.day > 12:
        return None
    try:
        swapped = date(d.year, d.day, d.month)
    except ValueError:
        return None
    return swapped if swapped <= today and swapped != d else None


def checklist_guidance(
    accounts: list[str],
    loaded_months: dict[str, set[str]],
    today: date,
    closed: dict[str, date] | None = None,
) -> list[Guidance]:
    """What the user should download next, most urgent first.

    Args:
        accounts: every account the user has, in display order. Accounts
            that only appear in `loaded_months` are added at the end.
        loaded_months: account -> set of 'YYYY-MM' months that have at
            least one movement (the Check List pivot, without counts).
        today: injected for testability.
        closed: account -> closing date. A closed account is never asked
            for: not behind, and its holes are summarised by
            ``closed_accounts`` instead of listed.

    The first item is the one the UI puts in evidence with the button;
    the others are listed below it. The list is never empty: when
    nothing is missing it holds a single `up_to_date` item.

    "Last month" is the previous calendar month: the current one is
    still running and no bank has closed its statement yet. The first
    round (nothing imported at all) suggests that month only, for every
    account, so the first pass through the workflow stays small.
    """
    closed = closed or {}
    loaded_months = without_future(loaded_months, today)
    every_account = list(accounts) + sorted(
        a for a in loaded_months if a not in set(accounts)
    )
    all_accounts = [a for a in every_account if a not in closed]
    if not every_account:
        return [Guidance(GuidanceKind.no_accounts)]
    if not all_accounts:
        # every account is closed: nothing left to download
        months = [_parse_ym(m) for ms in loaded_months.values() for m in ms]
        return [Guidance(GuidanceKind.up_to_date,
                         last_loaded=_first_day(max(months)) if months else None)]

    last_month = _shift(_ym(today), -1)
    one_month = (_first_day(last_month), _last_day(last_month))
    window_start = _shift(last_month, -(CHECK_WINDOW_MONTHS - 1))

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
            # Silent since before the window: the account may simply be closed,
            # so say so. If it is still in use, the download still resumes the
            # day after the last loaded month: starting at the window would
            # leave a hole of its own making.
            dormant = last < window_start
            behind.append((
                _months_between(last, last_month),
                Guidance(
                    GuidanceKind.dormant if dormant else GuidanceKind.behind, acc,
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
                run_end = _shift(ym, -1)
                if run_end >= window_start:  # older holes are not asked for
                    gaps.append(Guidance(
                        GuidanceKind.gap, acc,
                        date_from=_first_day(max(run_start, window_start)),
                        date_to=_last_day(run_end),
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


def check_window_start(today: date) -> date:
    """First day of the window the workflow asks about: the last
    CHECK_WINDOW_MONTHS complete months."""
    return _first_day(_shift(_shift(_ym(today), -1), -(CHECK_WINDOW_MONTHS - 1)))


def old_holes(
    accounts: list[str],
    loaded_months: dict[str, set[str]],
    today: date,
    closed: dict[str, date] | None = None,
) -> dict[str, int]:
    """Holes that end before the window, per active account: not asked for,
    only counted, so they are set aside rather than forgotten."""
    closed = closed or {}
    loaded_months = without_future(loaded_months, today)
    window_start = _shift(_shift(_ym(today), -1), -(CHECK_WINDOW_MONTHS - 1))
    every = list(accounts) + sorted(a for a in loaded_months if a not in set(accounts))
    result: dict[str, int] = {}
    for acc in every:
        if acc in closed:
            continue
        months = {_parse_ym(m) for m in loaded_months.get(acc, ())}
        if not months:
            continue
        count, in_hole = 0, False
        ym, last = min(months), max(months)
        while ym <= last and ym < window_start:
            missing = ym not in months
            if missing and not in_hole:
                count += 1
            in_hole = missing
            ym = _shift(ym, 1)
        # a hole still open at the window start reaches into the window: asked for
        if in_hole and ym == window_start and ym not in months:
            count -= 1
        if count:
            result[acc] = count
    return result


def group_by_account(guidance: list[Guidance]) -> list[tuple[str | None, list[Guidance]]]:
    """The same suggestions, one group per account, in order of first appearance.

    The urgency order of ``checklist_guidance`` decides which account comes
    first; within an account the suggestions keep their order. Without this an
    account behind by months and with a hole further back showed up in two
    places of the list.
    """
    groups: dict[str | None, list[Guidance]] = {}
    for g in guidance:
        groups.setdefault(g.account, []).append(g)
    return list(groups.items())


@dataclass(frozen=True)
class ClosedAccount:
    """A closed account and the holes it was closed with (runs of empty months
    between its first loaded month and its closing month). ``after_closing``
    is True when movements were loaded for a month after the closing one."""
    account: str
    closed_on: date
    holes: int
    after_closing: bool = False


def closed_accounts(
    closed: dict[str, date],
    loaded_months: dict[str, set[str]],
) -> list[ClosedAccount]:
    """The closed accounts in name order, each with how many holes it has."""
    result = []
    for acc in sorted(closed):
        months = {_parse_ym(m) for m in loaded_months.get(acc, ())}
        end = _ym(closed[acc])
        holes, in_hole = 0, False
        if months:
            ym = min(months)
            while ym <= end:
                missing = ym not in months
                if missing and not in_hole:
                    holes += 1
                in_hole = missing
                ym = _shift(ym, 1)
        after = bool(months) and max(months) > end
        result.append(ClosedAccount(acc, closed[acc], holes, after))
    return result


def suggested_closing_date(months: set[str], today: date) -> date:
    """End of the last month with movements; end of last month if none.
    Months after the current one are left out (see ``without_future``)."""
    months = {m for m in months if _parse_ym(m) <= _ym(today)}
    if months:
        return _last_day(max(_parse_ym(m) for m in months))
    return _last_day(_shift(_ym(today), -1))


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

# Counterparts seen once are not asked in the workflow up to an amount drawn
# from the user's own spending (founder, 2026-10-21): in a typical year of the
# last SMALL_SINGLES_YEARS, the single-movement counterparts up to that amount
# stay under SMALL_SINGLES_SHARE of the yearly spending. Deciding them means
# looking at movements one by one, for little money. They keep the proposed
# category and stay out of the goal; a switch shows them.
SMALL_SINGLES_SHARE = 0.05
SMALL_SINGLES_YEARS = 5


@dataclass(frozen=True)
class SmallSingles:
    """The counterparts with one movement left out of the workflow.

    `limit` is the threshold (0 when nothing is left out), `share` their part
    of the spending of the period shown.
    """
    descriptions: frozenset
    total: float
    share: float
    limit: float

    @property
    def count(self) -> int:
        return len(self.descriptions)


def small_single_limit(movements: list[tuple[str, str, float]],
                       share: float = SMALL_SINGLES_SHARE) -> float:
    """The threshold, from (year, counterpart key, amount) spending movements
    of the last years.

    A counterpart is single in a year when it has one movement that year.
    Taken from the smallest, the singles of all the years stay under *share*
    of the spending of all the years: per year, under *share* of the yearly
    average. A threshold never splits equal amounts. 0 without movements.
    """
    total = sum(a for _, _, a in movements)
    if total <= 0:
        return 0.0
    per_year: dict[tuple[str, str], list[float]] = {}
    for year, key, amount in movements:
        per_year.setdefault((year, key), []).append(amount)
    singles = sorted(v[0] for v in per_year.values() if len(v) == 1)
    limit, covered = 0.0, 0.0
    for amount in singles:
        if covered + amount >= share * total:
            if amount == limit:                 # equal amounts stay together
                limit = max((x for x in singles if x < amount), default=0.0)
            break
        covered += amount
        limit = amount
    return limit


def small_singles(stats: list[dict], limit: float) -> SmallSingles:
    """The single-movement counterparts of *stats* up to *limit*."""
    total = sum(s["total_amount"] for s in stats)
    taken = [s for s in stats if s["tx_count"] == 1 and s["total_amount"] <= limit] if limit > 0 else []
    hidden = sum(s["total_amount"] for s in taken)
    return SmallSingles(
        descriptions=frozenset(s["description"] for s in taken),
        total=hidden,
        share=hidden / total if total else 0.0,
        limit=limit if taken else 0.0,
    )


# Share of the period's spending the workflow suggests checking. Said to the
# user in words ("8 euros out of 10"), never as a percentage.
CHECK_GOAL_SHARE = 0.8


@dataclass(frozen=True)
class CheckProgress:
    """Spending categorized so far against the goal, in the weight given
    (spending movements in the workflow since 2026-10-20, money before).

    `missing` is how much is still to categorize to reach the goal and
    `needed` how many uncategorized counterparts, heaviest first, cover it.
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
    """Progress over (weight, categorized) pairs, one per counterpart.

    Ticking the heaviest uncategorized counterparts first reaches the goal
    with the fewest ticks, so `needed` counts them in that order; the list
    the user sees is in the same order, so "the first N" is true.
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

# Steps in order; the UI numbers only the steps listed here. "counterparts"
# checks the spending, "transfers" that the money moved between the user's
# own accounts has both sides, "income" the income left once the transfers
# are out: after them, so a transfer is never asked as income. Checking the
# movements is a flow of its own, not a step here (spec 31).
STEPS: list[str] = ["checklist", "import", "counterparts", "mixed", "transfers", "income", "report"]


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
