"""Tests for the guided workflow service: landing period, Home preset, Check List guidance."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from services.guided_flow_service import Guidance, GuidanceKind, GuidedFlowService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from ui.checklist_page import _fmt_date, _guidance_sentence
from ui.home_page import _PRESET_LAST_LOADED, _resolve_period
from ui.i18n import get_language, set_language

TODAY = date(2026, 10, 12)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _seed(engine, dates):
    with get_session(engine) as s:
        for i, d in enumerate(dates):
            s.add(Transaction(
                id=f"tx{i}", date=d, amount=Decimal(-10), currency="EUR",
                description="x", source_file="f.csv", doc_type="bank_statement",
                account_label="BancaX", tx_type="expense", to_review=False,
            ))
        s.commit()


def test_last_movement_date_empty_db(engine):
    assert TransactionService(engine).get_last_movement_date() is None


def test_last_movement_date_is_the_most_recent(engine):
    _seed(engine, ["2026-03-02", "2026-08-14", "2026-05-30"])
    assert TransactionService(engine).get_last_movement_date() == date(2026, 8, 14)


def test_landing_period_empty_db(engine):
    assert GuidedFlowService(engine).landing_period() is None


def test_landing_period_ends_with_last_movement_month(engine):
    _seed(engine, ["2026-03-02", "2026-08-14"])
    assert GuidedFlowService(engine).landing_period() == (date(2026, 6, 1), date(2026, 8, 31))


def test_stopped_at_when_last_month_is_missing(engine):
    _seed(engine, ["2026-08-14"])
    assert GuidedFlowService(engine).stopped_at(TODAY) == date(2026, 8, 14)


def test_not_stopped_when_last_month_is_loaded(engine):
    _seed(engine, ["2026-09-28"])
    assert GuidedFlowService(engine).stopped_at(TODAY) is None


def test_not_stopped_on_empty_db(engine):
    assert GuidedFlowService(engine).stopped_at(TODAY) is None


def test_home_last_loaded_preset_uses_landing():
    landing = (date(2026, 6, 1), date(2026, 8, 31))
    assert _resolve_period(_PRESET_LAST_LOADED, today=TODAY, landing=landing) == landing


def test_home_last_loaded_preset_without_data_falls_back_to_12_months():
    assert _resolve_period(_PRESET_LAST_LOADED, today=TODAY) == (date(2025, 10, 12), TODAY)


# ── Check List guidance ──────────────────────────────────────────────────────


def test_guidance_asks_for_accounts_on_empty_db(engine):
    result = GuidedFlowService(engine).checklist_guidance(TODAY)
    assert result == [Guidance(GuidanceKind.no_accounts)]


def test_add_account_by_name_only(engine):
    GuidedFlowService(engine).add_account("  BBVA  ")
    assert [a.name for a in SettingsService(engine).get_accounts()] == ["BBVA"]
    result = GuidedFlowService(engine).checklist_guidance(TODAY)
    assert [(g.kind, g.account) for g in result] == [(GuidanceKind.nothing_imported, "BBVA")]


def test_guidance_reads_months_from_movements(engine):
    SettingsService(engine).create_account("BancaX", "")
    _seed(engine, ["2026-07-10", "2026-08-14"])
    result = GuidedFlowService(engine).checklist_guidance(TODAY)
    assert result == [Guidance(
        GuidanceKind.behind, "BancaX",
        date_from=date(2026, 9, 1), date_to=TODAY, last_loaded=date(2026, 8, 1),
    )]


@pytest.fixture
def lang():
    before = get_language()
    yield set_language
    set_language(before)


@pytest.mark.parametrize("code, expected", [
    ("it", "1 settembre 2026"),
    ("en", "1 September 2026"),
    ("fr", "1 septembre 2026"),
    ("de", "1. September 2026"),
    ("es", "1 de septiembre de 2026"),
])
def test_long_date_per_language(lang, code, expected):
    lang(code)
    assert _fmt_date(date(2026, 9, 1)) == expected


@pytest.mark.parametrize("code", ["it", "en", "fr", "de", "es"])
@pytest.mark.parametrize("g", [
    Guidance(GuidanceKind.nothing_imported, "BBVA", date(2026, 9, 1), date(2026, 9, 30)),
    Guidance(GuidanceKind.never_loaded, "BBVA", date(2026, 9, 1), date(2026, 9, 30)),
    Guidance(GuidanceKind.behind, "BBVA", date(2026, 9, 1), TODAY, date(2026, 8, 1)),
    Guidance(GuidanceKind.gap, "BBVA", date(2026, 3, 1), date(2026, 3, 31), date(2026, 9, 1)),
    Guidance(GuidanceKind.up_to_date, last_loaded=date(2026, 9, 1)),
], ids=lambda g: g.kind.value)
def test_every_sentence_is_translated_and_filled(lang, code, g):
    lang(code)
    text = _guidance_sentence(g)
    assert not text.startswith("guided.")       # key not missing
    assert "{" not in text                      # every placeholder filled
    if g.account:
        assert "BBVA" in text


# ── Steps: persistence and resume ────────────────────────────────────────────

def test_resume_on_empty_db_starts_from_checklist(engine):
    SettingsService(engine).create_account("BancaX", "")
    assert GuidedFlowService(engine).resume(TODAY) == "checklist"


def test_saved_step_survives_a_new_service(engine):
    SettingsService(engine).create_account("BancaX", "")
    GuidedFlowService(engine).save_step("report")
    assert GuidedFlowService(engine).resume(TODAY) == "report"


def test_up_to_date_skips_checklist_and_import(engine):
    SettingsService(engine).create_account("BancaX", "")
    _seed(engine, ["2026-09-20"])
    svc = GuidedFlowService(engine)
    # No counterpart with mixed categories either: that step is closed too.
    assert svc.closed_steps(TODAY) == {"checklist", "import", "mixed"}
    svc.save_step("checklist")
    assert svc.resume(TODAY) == "counterparts"


def test_save_step_rejects_unknown_step(engine):
    with pytest.raises(ValueError):
        GuidedFlowService(engine).save_step("transfers")
