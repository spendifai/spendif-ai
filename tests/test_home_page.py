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
from services.transaction_service import NOT_COUNTED_TYPES
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

    def test_a_category_below_zero_is_never_a_source(self):
        """Refunds larger than the spending (8,678 back, 900 spent in Salute):
        neither the category nor its subcategories are drawn, so Salute does
        not show as where the money comes from (2026-10-21)."""
        df = self._df(
            (pd.Timestamp("2026-08-03"), 3000.0, "income", "Lavoro autonomo", None, False),
            (pd.Timestamp("2026-08-03"), 8678.0, "income", "Salute", "Altro salute", False),
            (pd.Timestamp("2026-08-10"), -800.0, "expense", "Salute", "Dentista", False),
            (pd.Timestamp("2026-08-12"), -100.0, "expense", "Alimentari", "Spesa", False),
        )
        out = _build_sankey_data(df, expense_categories={"Salute", "Alimentari"})
        sources = {out["nodes"][i] for i in out["link_source"]}
        assert not any("Salute" in n or "Dentista" in n for n in sources)
        assert not any("Dentista" in n for n in out["nodes"])

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

    def test_card_spending_counts_and_settlement_does_not(self):
        """Card movements are spending like any other, reconciled or not; the
        settlement debit on the bank account is a giroconto and counts nowhere."""
        df = self._df(
            (pd.Timestamp("2026-01-15"), 500.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-20"), -200.0, "card_tx", "Alimentari", "Spesa", True),
            (pd.Timestamp("2026-01-21"), -100.0, "card_tx", "Trasporti", "ATM", False),
            (pd.Timestamp("2026-01-31"), -300.0, "card_settlement", None, None, True),
        )
        out = _build_sankey_data(df)
        assert out["unknown_kind"] == "surplus"
        unknown = out["nodes"].index(_t("home.charts.unknown"))
        assert out["link_value"][out["link_target"].index(unknown)] == 200.0

    def test_surplus_is_the_registry_net_balance(self):
        """The Sankey surplus is all income minus all spending of the period,
        giroconti left out: the Registry's net balance for the same period."""
        rows = [
            (pd.Timestamp("2026-01-15"), 1000.0, "income", "Stipendio", None, False),
            (pd.Timestamp("2026-01-16"), 40.0, "income", "Alimentari", None, False),  # refund
            (pd.Timestamp("2026-01-20"), -250.0, "expense", "Alimentari", None, False),
            (pd.Timestamp("2026-01-21"), -120.0, "card_tx", "Trasporti", None, False),
            (pd.Timestamp("2026-01-22"), -60.0, "unknown", None, None, False),
            (pd.Timestamp("2026-01-23"), -500.0, "internal_out", None, None, False),
            (pd.Timestamp("2026-01-24"), 500.0, "internal_in", None, None, False),
        ]
        out = _build_sankey_data(self._df(*rows), {"Alimentari", "Trasporti"})
        unknown = out["nodes"].index(_t("home.charts.unknown"))
        surplus = out["link_value"][out["link_target"].index(unknown)]
        net = sum(r[1] for r in rows if r[2] not in NOT_COUNTED_TYPES)
        assert surplus == pytest.approx(net) == 610.0

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

    def test_transfers_left_out(self):
        df = _rows(
            (pd.Timestamp("2026-01-10"), 5000.0, "internal_in", "Giroconto", None, False),
            (pd.Timestamp("2026-01-11"), -5000.0, "internal_out", "Giroconto", None, False),
            (pd.Timestamp("2026-01-12"), -800.0, "card_settlement", None, None, True),
        )
        assert _build_monthly_flow(df, {"Alimentari"}) is None

    def test_card_spending_counts(self):
        df = _rows((pd.Timestamp("2026-01-20"), -200.0, "card_tx", "Alimentari", None, True))
        assert _build_monthly_flow(df, {"Alimentari"})["expense"].iloc[0]["Alimentari"] == 200.0

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


class TestSankeySidesAndLevels:
    """Where the money comes from on the left, where it goes on the right;
    subcategories only on request."""

    @staticmethod
    def _df(*rows):
        return pd.DataFrame(list(rows), columns=["date", "amount", "tx_type", "category", "subcategory", "reconciled"])

    _ROWS = (
        (pd.Timestamp("2026-01-10"), 3000.0, "income", "Lavoro dipendente", "Stipendio", False),
        (pd.Timestamp("2026-01-11"), 500.0, "income", "Lavoro autonomo", "Parcella", False),
        (pd.Timestamp("2026-01-20"), -800.0, "expense", "Casa", "Affitto", False),
        (pd.Timestamp("2026-01-21"), -200.0, "card_tx", "Alimentari", "Spesa", False),
    )

    def _flows(self, out):
        n = out["nodes"]
        return {(n[s], n[t]): v for s, t, v in zip(out["link_source"], out["link_target"], out["link_value"])}

    def test_income_categories_feed_the_total(self):
        out = _build_sankey_data(self._df(*self._ROWS), {"Casa", "Alimentari"})
        flows = self._flows(out)
        total = _t("home.charts.income_total")
        assert flows[("Lavoro dipendente", total)] == 3000.0
        assert flows[("Lavoro autonomo", total)] == 500.0
        assert flows[(total, "Casa")] == 800.0

    def test_categories_only_by_default(self):
        out = _build_sankey_data(self._df(*self._ROWS), {"Casa", "Alimentari"})
        kinds = {k[0] for k in out["node_kinds"]}
        assert "subcategory" not in kinds and "in_subcategory" not in kinds

    def test_subcategories_on_both_sides_on_request(self):
        out = _build_sankey_data(self._df(*self._ROWS), {"Casa", "Alimentari"}, subcategories=True)
        flows = self._flows(out)
        assert flows[("Stipendio (Lavoro dipendente)", "Lavoro dipendente")] == 3000.0
        assert flows[("Casa", "Affitto (Casa)")] == 800.0

    def test_a_deficit_is_a_source_next_to_income(self):
        rows = self._ROWS + ((pd.Timestamp("2026-01-25"), -4000.0, "expense", "Casa", "Mutuo", False),)
        out = _build_sankey_data(self._df(*rows), {"Casa", "Alimentari"})
        flows = self._flows(out)
        assert flows[(_t("home.charts.unknown"), _t("home.charts.income_total"))] == 1500.0

    def test_same_name_income_and_spending_are_two_nodes(self):
        rows = self._ROWS + (
            (pd.Timestamp("2026-01-26"), -300.0, "expense", "Investimenti e risparmi", "Fondi", False),
            (pd.Timestamp("2026-01-27"), 40.0, "income", "Investimenti e risparmi", "Interessi", False),
        )
        out = _build_sankey_data(self._df(*rows), {"Casa", "Alimentari"})
        assert out["nodes"].count("Investimenti e risparmi") == 2

    def test_spending_only_drops_the_sources(self):
        out = _build_sankey_data(self._df(*self._ROWS), {"Casa", "Alimentari"}, spending_only=True)
        kinds = {k[0] for k in out["node_kinds"]}
        assert "in_category" not in kinds
        flows = self._flows(out)
        assert flows[(_t("home.charts.income_total"), "Casa")] == 800.0

    def test_every_column_reads_from_the_biggest_branch_down(self):
        from ui.home_page import _sankey_layout
        rows = self._ROWS + (
            (pd.Timestamp("2026-01-26"), -1200.0, "expense", "Investimenti e risparmi", "Fondi", False),
            (pd.Timestamp("2026-01-27"), -50.0, "expense", "Casa", "Acqua", False),
        )
        out = _build_sankey_data(self._df(*rows), {"Casa", "Alimentari"}, subcategories=True)
        x, y, size, _ = _sankey_layout(out)
        kinds = out["node_kinds"]
        cats = sorted((i for i, k in enumerate(kinds) if k[0] == "category"), key=lambda i: y[i])
        assert [size[i] for i in cats] == sorted((size[i] for i in cats), reverse=True)
        incomes = sorted((i for i, k in enumerate(kinds) if k[0] == "in_category"), key=lambda i: y[i])
        assert [kinds[i][1] for i in incomes] == ["Lavoro dipendente", "Lavoro autonomo"]
        # Casa's subcategories stay together, under the bigger Investimenti one
        subs = sorted((i for i, k in enumerate(kinds) if k[0] == "subcategory"), key=lambda i: y[i])
        assert [kinds[i][1] for i in subs][:1] == ["Investimenti e risparmi"]
        assert [kinds[i][2] for i in subs if kinds[i][1] == "Casa"] == ["Affitto", "Acqua"]

    def test_labels_carry_euro_or_percent_only_where_they_fit(self):
        from ui.home_page import _sankey_labels, _sankey_layout
        rows = self._ROWS + ((pd.Timestamp("2026-01-27"), -5.0, "expense", "Svago", None, False),)
        out = _build_sankey_data(self._df(*rows), {"Casa", "Alimentari", "Svago"})
        _, _, size, height = _sankey_layout(out)
        euro = _sankey_labels(out, size, height, 600, lambda v: f"{v:.0f} EUR")
        pct = _sankey_labels(out, size, height, 600, lambda v: f"{v:.0f} EUR", percent=True)
        casa = out["nodes"].index("Casa")
        assert euro[casa] == "Casa  800 EUR"
        assert pct[casa] == "Casa  23%"                     # 800 of 3500
        svago = out["nodes"].index("Svago")
        assert euro[svago] == "Svago"                       # 5 euro: no room
        sub = _build_sankey_data(self._df(*rows), {"Casa", "Alimentari", "Svago"}, subcategories=True)
        _, _, s2, h2 = _sankey_layout(sub)
        assert _sankey_labels(sub, s2, h2, 600, str) == sub["nodes"]

    def test_nodes_stay_inside_the_chart(self):
        from ui.home_page import _SANKEY_PAD_PX, _sankey_columns, _sankey_layout, _sankey_plot_px
        rows = list(self._ROWS) + [
            (pd.Timestamp("2026-01-20"), -float(10 + i), "expense", f"C{i}", f"S{i}", False) for i in range(25)
        ]
        out = _build_sankey_data(self._df(*rows), {f"C{i}" for i in range(25)} | {"Casa", "Alimentari"},
                                 subcategories=True)
        plot_px = _sankey_plot_px(out)
        _, y, _, h = _sankey_layout(out, plot_px)
        columns, _ = _sankey_columns(out)
        for c in set(columns):
            nodes = sorted((i for i in range(len(y)) if columns[i] == c), key=lambda i: y[i])
            bottom = max(y[i] + h[i] / 2 for i in nodes)
            assert bottom <= 1.0 + 1e-9                        # nothing below the chart
            for a, b in zip(nodes, nodes[1:]):                 # no overlap, the pad kept
                gap = (y[b] - h[b] / 2) - (y[a] + h[a] / 2)
                assert gap * plot_px >= _SANKEY_PAD_PX - 1e-6
