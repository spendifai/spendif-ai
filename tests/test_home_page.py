"""Tests for ui/home_page.py — pure helpers around the dashboard.

We don't test the Streamlit render branches (st.plotly_chart, st.button,
…) — those follow the project policy of leaving Streamlit-widget code to
the runtime. Coverage focuses on the deterministic data-shaping helpers
that drive the charts: `_load_transactions`, `_build_sankey_data`,
`_twelve_months_ago`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine

from db.models import Transaction, create_tables, get_session
from ui.home_page import (
    _build_monthly_flow,
    _build_sankey_data,
    _load_transactions,
    _twelve_months_ago,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _make_tx(tx_id, date_iso, amount, *, tx_type="expense",
             category=None, subcategory=None, reconciled=False):
    return Transaction(
        id=tx_id, date=date_iso, amount=Decimal(str(amount)),
        currency="EUR", description="x", source_file="f.csv",
        doc_type="bank_statement", account_label="BancaX",
        tx_type=tx_type, category=category, subcategory=subcategory,
        reconciled=reconciled, to_review=False,
    )


def _seed(engine, rows):
    with get_session(engine) as s:
        for r in rows:
            s.add(r)
        s.commit()


# ── _twelve_months_ago ────────────────────────────────────────────────────────

class TestTwelveMonthsAgo:

    def test_handles_standard_date(self):
        # 2026-05-15 → 2025-05-15
        got = _twelve_months_ago(date(2026, 5, 15))
        assert got == date(2025, 5, 15)

    def test_feb29_falls_back_to_365_days(self):
        # 2024-02-29 → 2023 has no Feb 29 → fallback to 365 days earlier
        got = _twelve_months_ago(date(2024, 2, 29))
        assert got == date(2024, 2, 29) - timedelta(days=365)


# ── _load_transactions ───────────────────────────────────────────────────────

class TestLoadTransactions:

    def test_empty_db_returns_empty_dataframe(self, engine):
        df = _load_transactions(engine)
        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert list(df.columns) == ["date", "amount", "tx_type", "category", "subcategory", "reconciled"]

    def test_clamps_to_12_months(self, engine):
        """Rows older than 12 months are filtered out at the SQL level."""
        # since = today - 365 days, so 400 days ago is well outside
        old_date = (date.today() - timedelta(days=400)).isoformat()
        recent_date = (date.today() - timedelta(days=30)).isoformat()
        _seed(engine, [
            _make_tx("a" * 24, old_date, -50, category="Vecchio"),
            _make_tx("b" * 24, recent_date, -50, category="Recente"),
        ])
        df = _load_transactions(engine)
        assert len(df) == 1
        assert df.iloc[0]["category"] == "Recente"

    def test_returns_floats_for_amount(self, engine):
        """SQLAlchemy Numeric returns Decimal; the dataframe converts to float."""
        _seed(engine, [
            _make_tx("a" * 24, (date.today() - timedelta(days=30)).isoformat(),
                     "-12.34", category="Alimentari"),
        ])
        df = _load_transactions(engine)
        assert df.iloc[0]["amount"] == pytest.approx(-12.34)
        assert isinstance(df.iloc[0]["amount"], float)


# ── _build_sankey_data ───────────────────────────────────────────────────────

class TestBuildSankeyData:

    def _df(self, *rows):
        return pd.DataFrame(
            list(rows),
            columns=["date", "amount", "tx_type", "category", "subcategory", "reconciled"],
        )

    def test_no_income_no_expenses_returns_none(self):
        empty = self._df()
        assert _build_sankey_data(empty) is None

    def test_income_only_marks_surplus(self):
        """Only income, zero expenses → surplus = total income, Unknown is
        a target node consuming the whole flow."""
        df = self._df(
            (pd.Timestamp("2026-01-15"), 1000.0, "income", "Stipendio", None, False),
        )
        out = _build_sankey_data(df)
        assert out is not None
        assert out["unknown_kind"] == "surplus"
        # Total flow value into Unknown == total income
        from ui.home_page import _t as _t_for_test
        unk_label = _t_for_test("home.charts.unknown")
        unk_idx = out["nodes"].index(unk_label)
        flow_to_unknown = sum(
            v for s, t, v in zip(out["link_source"], out["link_target"], out["link_value"])
            if t == unk_idx
        )
        assert flow_to_unknown == 1000.0

    def test_surplus_when_income_gt_expense(self):
        df = self._df(
            (pd.Timestamp("2026-01-15"), 1000.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -300.0, "expense", "Alimentari", "Spesa", False),
        )
        out = _build_sankey_data(df)
        from ui.home_page import _t as _t_for_test
        unk_label = _t_for_test("home.charts.unknown")
        assert out["unknown_kind"] == "surplus"
        unk_idx = out["nodes"].index(unk_label)
        # Surplus = 1000 - 300 = 700 → income → unknown link
        surplus_flow = next(
            v for s, t, v in zip(out["link_source"], out["link_target"], out["link_value"])
            if t == unk_idx and s == 0
        )
        assert surplus_flow == 700.0

    def test_deficit_when_expense_gt_income(self):
        """Expenses larger than income → Unknown is a *source* feeding the
        missing budget into the "Total income" node."""
        df = self._df(
            (pd.Timestamp("2026-01-15"), 100.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -300.0, "expense", "Alimentari", "Spesa", False),
        )
        out = _build_sankey_data(df)
        from ui.home_page import _t as _t_for_test
        unk_label = _t_for_test("home.charts.unknown")
        assert out["unknown_kind"] == "deficit"
        unk_idx = out["nodes"].index(unk_label)
        # Deficit = 200 → unknown → income node flow
        deficit_flow = next(
            v for s, t, v in zip(out["link_source"], out["link_target"], out["link_value"])
            if s == unk_idx and t == 0
        )
        assert deficit_flow == 200.0

    def test_parity_omits_unknown_node(self):
        """income == expense → no surplus, no deficit → Unknown is omitted."""
        df = self._df(
            (pd.Timestamp("2026-01-15"), 500.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -500.0, "expense", "Alimentari", "Spesa", False),
        )
        out = _build_sankey_data(df)
        from ui.home_page import _t as _t_for_test
        unk_label = _t_for_test("home.charts.unknown")
        assert out["unknown_kind"] == "parity"
        # Unknown label may still appear (if it was already a category name) but
        # there must be NO link involving it as source or target.
        if unk_label in out["nodes"]:
            unk_idx = out["nodes"].index(unk_label)
            assert not any(s == unk_idx or t == unk_idx
                           for s, t in zip(out["link_source"], out["link_target"]))

    def test_internal_in_is_not_counted_as_income(self):
        """internal_in (giroconto) is not real income; the surplus calc must
        ignore it."""
        df = self._df(
            # internal_in 5000 should be ignored
            (pd.Timestamp("2026-01-10"), 5000.0, "internal_in", "Giroconto", None, False),
            (pd.Timestamp("2026-01-15"), 100.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -200.0, "expense", "Alimentari", "Spesa", False),
        )
        out = _build_sankey_data(df)
        # 100 income, 200 expense → deficit 100, NOT a surplus from the 5000
        assert out["unknown_kind"] == "deficit"

    def test_reconciled_transactions_excluded(self):
        """Reconciled rows (already settled by card reconciliation) must not
        be double-counted."""
        df = self._df(
            (pd.Timestamp("2026-01-15"), 500.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -200.0, "expense", "Alimentari", "Spesa", True),  # reconciled
            (pd.Timestamp("2026-01-21"), -50.0, "expense", "Trasporti", "ATM", False),
        )
        out = _build_sankey_data(df)
        # Expected expense = 50, income = 500 → surplus 450
        assert out["unknown_kind"] == "surplus"

    def test_refund_lowers_its_expense_category(self):
        """An income movement with an expense category is money back on a
        purchase: it lowers that expense, it is not income."""
        df = self._df(
            (pd.Timestamp("2026-01-15"), 500.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -200.0, "expense", "Alimentari", "Spesa", False),
            (pd.Timestamp("2026-01-25"), 50.0, "income", "Alimentari", "Spesa", False),
        )
        out = _build_sankey_data(df, {"Alimentari"})
        i = out["nodes"].index("Alimentari")
        flows = dict(zip(out["link_target"], out["link_value"]))
        assert flows[i] == 150.0
        unknown = out["nodes"].index(_t("home.charts.unknown"))
        assert flows[unknown] == 350.0      # 500 income - 150 spent


def _t(key):
    from ui.home_page import _t as t
    return t(key)


# ── _build_monthly_flow ──────────────────────────────────────────────────────

def _rows(*rows):
    return pd.DataFrame(
        list(rows),
        columns=["date", "amount", "tx_type", "category", "subcategory", "reconciled"],
    )


class TestBuildMonthlyFlow:

    def test_income_and_expenses_by_month(self):
        df = _rows(
            (pd.Timestamp("2026-01-15"), 1000.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -200.0, "expense", "Alimentari", "Spesa", False),
            (pd.Timestamp("2026-02-20"), -80.0, "expense", "Trasporti", None, False),
        )
        out = _build_monthly_flow(df, {"Alimentari", "Trasporti"})
        jan, feb = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01")
        assert list(out["income"].index) == [jan, feb]
        assert out["income"].loc[jan, "Stipendio"] == 1000.0
        assert out["income"].loc[feb, "Stipendio"] == 0.0
        assert out["expense"].loc[jan, "Alimentari"] == 200.0
        assert out["expense"].loc[feb, "Trasporti"] == 80.0

    def test_refund_is_not_income(self):
        df = _rows(
            (pd.Timestamp("2026-01-20"), -200.0, "expense", "Alimentari", "Spesa", False),
            (pd.Timestamp("2026-01-25"), 50.0, "income", "Alimentari", "Spesa", False),
        )
        out = _build_monthly_flow(df, {"Alimentari"})
        assert out["income"].empty or "Alimentari" not in out["income"].columns
        assert out["expense"].iloc[0]["Alimentari"] == 150.0

    def test_refund_larger_than_spending_counts_zero(self):
        df = _rows(
            (pd.Timestamp("2026-01-20"), -20.0, "expense", "Alimentari", None, False),
            (pd.Timestamp("2026-01-25"), 50.0, "income", "Alimentari", None, False),
            (pd.Timestamp("2026-01-26"), -30.0, "expense", "Trasporti", None, False),
        )
        out = _build_monthly_flow(df, {"Alimentari", "Trasporti"})
        assert "Alimentari" not in out["expense"].columns
        assert out["expense"].iloc[0]["Trasporti"] == 30.0

    def test_transfers_and_reconciled_left_out(self):
        df = _rows(
            (pd.Timestamp("2026-01-10"), 5000.0, "internal_in", "Giroconto", None, False),
            (pd.Timestamp("2026-01-11"), -5000.0, "internal_out", "Giroconto", None, False),
            (pd.Timestamp("2026-01-20"), -200.0, "expense", "Alimentari", None, True),
        )
        assert _build_monthly_flow(df, {"Alimentari"}) is None

    def test_small_categories_go_to_other(self):
        rows = [(pd.Timestamp("2026-01-20"), -float(10 * (i + 1)), "expense", f"C{i}", None, False)
                for i in range(5)]
        out = _build_monthly_flow(_rows(*rows), {f"C{i}" for i in range(5)}, top=3)
        other = _t("home.charts.other")
        assert list(out["expense"].columns) == ["C4", "C3", "C2", other]
        assert out["expense"].iloc[0][other] == 30.0     # 10 + 20

    def test_uncategorised_has_a_label(self):
        df = _rows((pd.Timestamp("2026-01-20"), -20.0, "expense", None, None, False))
        out = _build_monthly_flow(df, set())
        assert list(out["expense"].columns) == [_t("home.charts.uncategorised")]
