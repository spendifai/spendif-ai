"""Closing an account: the Check List stops asking for it, reopening brings it back."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, text

from core.guided_flow import (
    GuidanceKind,
    checklist_guidance,
    closed_accounts,
    suggested_closing_date,
)
from db.models import Transaction, create_tables, get_session
from services.guided_flow_service import GuidedFlowService
from services.settings_service import SettingsService

TODAY = date(2026, 10, 14)


# ── Pure logic ───────────────────────────────────────────────────────────────

def test_closed_account_is_never_behind():
    loaded = {"Amex": {"2026-02", "2026-03"}, "BBVA": {"2026-09"}}
    guidance = checklist_guidance(["Amex", "BBVA"], loaded, TODAY,
                                  closed={"Amex": date(2026, 3, 31)})
    assert all(g.account != "Amex" for g in guidance)
    assert guidance[0].kind is GuidanceKind.up_to_date


def test_closed_account_holes_are_not_asked():
    loaded = {"Amex": {"2025-01", "2025-06", "2026-03"}, "BBVA": {"2026-09"}}
    guidance = checklist_guidance(["Amex", "BBVA"], loaded, TODAY,
                                  closed={"Amex": date(2026, 3, 31)})
    assert [g.kind for g in guidance] == [GuidanceKind.up_to_date]


def test_every_account_closed_is_up_to_date_not_no_accounts():
    guidance = checklist_guidance(["Amex"], {"Amex": {"2026-03"}}, TODAY,
                                  closed={"Amex": date(2026, 3, 31)})
    assert guidance[0].kind is GuidanceKind.up_to_date


def test_closed_summary_counts_the_holes():
    loaded = {"Amex": {"2025-01", "2025-03", "2025-06"}}
    [c] = closed_accounts({"Amex": date(2025, 8, 31)}, loaded)
    # Feb 2025, Apr-May 2025, Jul-Aug 2025 (up to the closing month)
    assert c.holes == 3
    assert c.after_closing is False


def test_movements_after_closing_are_flagged():
    [c] = closed_accounts({"Amex": date(2026, 3, 31)}, {"Amex": {"2026-03", "2026-05"}})
    assert c.after_closing is True


def test_suggested_closing_date_is_end_of_last_loaded_month():
    assert suggested_closing_date({"2026-01", "2026-03"}, TODAY) == date(2026, 3, 31)
    assert suggested_closing_date(set(), TODAY) == date(2026, 9, 30)


# ── Persistence ──────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _seed(engine, account, months):
    with get_session(engine) as s:
        for i, ym in enumerate(months):
            s.add(Transaction(
                id=f"{account}{i}", date=f"{ym}-10", amount=Decimal(-10), currency="EUR",
                description="x", source_file="f.csv", doc_type="bank_statement",
                account_label=account, tx_type="expense", to_review=False,
            ))
        s.commit()


def test_close_and_reopen_through_the_service(engine):
    SettingsService(engine).create_account("Amex", "")
    _seed(engine, "Amex", ["2026-02", "2026-03"])
    _seed(engine, "BBVA", ["2026-09"])
    svc = GuidedFlowService(engine)
    assert any(g.account == "Amex" for g in svc.checklist_guidance(TODAY))

    svc.close_account("Amex", date(2026, 3, 31))
    assert all(g.account != "Amex" for g in svc.checklist_guidance(TODAY))
    assert [c.account for c in svc.closed_accounts()] == ["Amex"]

    svc.reopen_account("Amex")
    assert any(g.account == "Amex" for g in svc.checklist_guidance(TODAY))
    assert svc.closed_accounts() == []


def test_closing_an_account_seen_only_in_movements_registers_it(engine):
    _seed(engine, "Edenred", ["2026-01"])
    svc = GuidedFlowService(engine)
    svc.close_account("Edenred", date(2026, 1, 31))
    names = [a.name for a in SettingsService(engine).get_accounts()]
    assert names == ["Edenred"]
    assert all(g.account != "Edenred" for g in svc.checklist_guidance(TODAY))


def test_migration_adds_the_column_to_an_existing_db(tmp_path):
    db = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY, name VARCHAR(256) "
                          "UNIQUE NOT NULL, bank_name VARCHAR(256), created_at DATETIME)"))
        conn.execute(text("INSERT INTO account (name) VALUES ('Amex')"))
    create_tables(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("account")}
    assert "closed_on" in cols
    assert [a.name for a in SettingsService(eng).get_accounts()] == ["Amex"]
