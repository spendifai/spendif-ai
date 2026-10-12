"""Tests for core/guided_flow.py: Check List guidance, reference period, resume."""
from __future__ import annotations

from datetime import date

import pytest

from core.guided_flow import (
    Guidance,
    GuidanceKind,
    checklist_guidance,
    is_stale,
    reference_period,
    resume_step,
)

TODAY = date(2026, 10, 12)


# ── Check List guidance ──────────────────────────────────────────────────────

def test_no_accounts_asks_for_them():
    assert checklist_guidance([], {}, TODAY) == [Guidance(GuidanceKind.no_accounts)]


def test_first_round_suggests_last_month_only_for_every_account():
    result = checklist_guidance(["BBVA", "Fineco"], {}, TODAY)
    assert [g.kind for g in result] == [GuidanceKind.nothing_imported] * 2
    assert [g.account for g in result] == ["BBVA", "Fineco"]
    for g in result:
        assert (g.date_from, g.date_to) == (date(2026, 9, 1), date(2026, 9, 30))


def test_up_to_date_when_last_month_is_loaded():
    loaded = {"BBVA": {"2026-08", "2026-09"}, "Fineco": {"2026-09"}}
    result = checklist_guidance(["BBVA", "Fineco"], loaded, TODAY)
    assert result == [Guidance(GuidanceKind.up_to_date, last_loaded=date(2026, 9, 1))]


def test_current_month_is_never_required():
    # Loaded up to September on 12 October: nothing missing.
    result = checklist_guidance(["BBVA"], {"BBVA": {"2026-09"}}, TODAY)
    assert result[0].kind is GuidanceKind.up_to_date


def test_behind_suggests_from_first_day_after_last_loaded_month():
    result = checklist_guidance(["BBVA"], {"BBVA": {"2026-07", "2026-08"}}, TODAY)
    assert result == [Guidance(
        GuidanceKind.behind, "BBVA",
        date_from=date(2026, 9, 1), date_to=TODAY, last_loaded=date(2026, 8, 1),
    )]


def test_most_behind_account_comes_first():
    loaded = {"BBVA": {"2026-08"}, "Fineco": {"2026-03"}, "Amex": {"2026-09"}}
    result = checklist_guidance(["BBVA", "Fineco", "Amex"], loaded, TODAY)
    assert [(g.kind, g.account) for g in result] == [
        (GuidanceKind.behind, "Fineco"),
        (GuidanceKind.behind, "BBVA"),
    ]


def test_never_loaded_account_comes_before_behind_ones():
    loaded = {"BBVA": {"2026-01"}}
    result = checklist_guidance(["BBVA", "Satispay"], loaded, TODAY)
    assert [(g.kind, g.account) for g in result] == [
        (GuidanceKind.never_loaded, "Satispay"),
        (GuidanceKind.behind, "BBVA"),
    ]
    assert (result[0].date_from, result[0].date_to) == (date(2026, 9, 1), date(2026, 9, 30))


def test_gap_in_the_middle_is_one_month_range():
    loaded = {"Fineco": {"2026-02", "2026-04", "2026-09"}}
    result = checklist_guidance(["Fineco"], loaded, TODAY)
    gaps = [g for g in result if g.kind is GuidanceKind.gap]
    assert [(g.date_from, g.date_to) for g in gaps] == [
        (date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 5, 1), date(2026, 8, 31)),   # consecutive months merged
    ]


def test_gap_across_year_end():
    loaded = {"BBVA": {"2025-11", "2026-02", "2026-09"}}
    result = checklist_guidance(["BBVA"], loaded, TODAY)
    gaps = [g for g in result if g.kind is GuidanceKind.gap]
    assert (gaps[0].date_from, gaps[0].date_to) == (date(2025, 12, 1), date(2026, 1, 31))


def test_behind_comes_before_gaps():
    loaded = {"BBVA": {"2026-01", "2026-03"}}
    result = checklist_guidance(["BBVA"], loaded, TODAY)
    assert [g.kind for g in result] == [GuidanceKind.behind, GuidanceKind.gap]


def test_accounts_only_seen_in_movements_are_included():
    # A label in the data that was never declared as an account.
    result = checklist_guidance([], {"Old bank": {"2026-05"}}, TODAY)
    assert [(g.kind, g.account) for g in result] == [(GuidanceKind.behind, "Old bank")]


def test_january_last_month_is_december_of_previous_year():
    result = checklist_guidance(["BBVA"], {}, date(2027, 1, 5))
    assert (result[0].date_from, result[0].date_to) == (date(2026, 12, 1), date(2026, 12, 31))


# ── Reference period ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("last, expected", [
    (date(2026, 8, 14), (date(2026, 6, 1), date(2026, 8, 31))),
    (date(2026, 8, 31), (date(2026, 6, 1), date(2026, 8, 31))),
    (date(2026, 8, 1), (date(2026, 6, 1), date(2026, 8, 31))),
    (date(2026, 2, 3), (date(2025, 12, 1), date(2026, 2, 28))),   # across year end
    (date(2028, 2, 10), (date(2027, 12, 1), date(2028, 2, 29))),  # leap year
])
def test_reference_period_is_three_months_ending_with_last_movement(last, expected):
    assert reference_period(last) == expected


@pytest.mark.parametrize("last, stale", [
    (date(2026, 10, 3), False),
    (date(2026, 9, 1), False),
    (date(2026, 8, 31), True),
    (date(2025, 12, 20), True),
])
def test_is_stale_uses_last_month_threshold(last, stale):
    assert is_stale(last, TODAY) is stale


# ── Resume ───────────────────────────────────────────────────────────────────

STEPS = ["checklist", "import", "counterparts", "rules", "report"]


def test_resume_returns_saved_step_when_still_open():
    assert resume_step(STEPS, "counterparts", set()) == "counterparts"


def test_resume_skips_steps_closed_by_the_data():
    assert resume_step(STEPS, "counterparts", {"counterparts"}) == "rules"


def test_resume_without_saved_step_starts_from_first_open():
    assert resume_step(STEPS, None, {"checklist"}) == "import"


def test_resume_unknown_saved_step_starts_from_beginning():
    assert resume_step(STEPS, "transfers", set()) == "checklist"


def test_resume_restarts_cycle_when_everything_after_is_closed():
    assert resume_step(STEPS, "rules", {"rules", "report"}) == "checklist"


def test_resume_rejects_empty_steps():
    with pytest.raises(ValueError):
        resume_step([], None, set())
