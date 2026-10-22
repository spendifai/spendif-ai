"""Home page: landing dashboard with monthly income and expenses + income→expense Sankey.

Two charts. The default period is the three months ending with the last
imported movement, so a user who has not imported for a while sees where
they left off instead of an empty window:

  1. Monthly bars: income above zero, expenses below, each split by
     category (the largest kept, the rest in "Other"). Transfers between own
     accounts are neither; an income movement with an expense category is a
     refund and lowers that expense.
  2. Sankey from "Total income" through expense categories down to
     subcategories, plus a "Sconosciuto" node that absorbs the surplus
     (or supplies the deficit) so the chart balances.

Empty state shows up when the `transaction` table is empty; in production
the routing in app.py keeps the user on Import in that case, but defending
the direct-URL access is cheap.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import pandas as pd
import streamlit as st

from services.guided_flow_service import GuidedFlowService
from services.settings_service import SettingsService
from services.transaction_service import NOT_COUNTED_TYPES, TransactionService
from ui.i18n import t


# ── i18n helper (alias to keep call sites short) ────────────────────────────
def _t(key: str, **kwargs):
    return t(key, **kwargs)


# ── Constants ────────────────────────────────────────────────────────────────

_ROLLING_DAYS = 365  # rolling 12-month window


# ── Date range helpers ───────────────────────────────────────────────────────

# Preset keys used both in session_state and in the i18n labels.
_PRESET_LAST_LOADED  = "last_loaded"    # default: where the user left off
_PRESET_CURRENT_YEAR  = "current_year"
_PRESET_CURRENT_QTR   = "current_quarter"
_PRESET_CURRENT_MONTH = "current_month"
_PRESET_LAST_12M      = "last_12_months"
_PRESET_CUSTOM        = "custom"
PRESETS_ORDERED = [
    _PRESET_LAST_LOADED,
    _PRESET_CURRENT_YEAR,
    _PRESET_CURRENT_QTR,
    _PRESET_CURRENT_MONTH,
    _PRESET_LAST_12M,
    _PRESET_CUSTOM,
]


def _twelve_months_ago(today: Optional[date] = None) -> date:
    """Return today minus 12 months. Uses replace(year=year-1) when possible,
    falling back to a 365-day delta for Feb-29 corner case."""
    today = today or date.today()
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        # Feb 29 in a leap year
        return today - timedelta(days=_ROLLING_DAYS)


def _resolve_period(
    preset: str,
    today: Optional[date] = None,
    custom_from: Optional[date] = None,
    custom_to: Optional[date] = None,
    landing: Optional[tuple[date, date]] = None,
) -> tuple[date, date]:
    """Return the (date_from, date_to) tuple for the chosen preset.

    "last_loaded" returns `landing`, the three months ending with the last
    movement (GuidedFlowService.landing_period); without it, the last 12
    months. For the other built-in presets `date_to` is `today` (data in
    the future has no meaning for a personal-finance dashboard). The
    "custom" preset is the only one that honours `custom_to`.
    """
    today = today or date.today()
    if preset == _PRESET_LAST_LOADED:
        return landing or (_twelve_months_ago(today), today)
    if preset == _PRESET_CURRENT_YEAR:
        return date(today.year, 1, 1), today
    if preset == _PRESET_CURRENT_QTR:
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return date(today.year, q_start_month, 1), today
    if preset == _PRESET_CURRENT_MONTH:
        return date(today.year, today.month, 1), today
    if preset == _PRESET_LAST_12M:
        return _twelve_months_ago(today), today
    if preset == _PRESET_CUSTOM:
        return (custom_from or _twelve_months_ago(today)), (custom_to or today)
    # Unknown preset → safe default
    return date(today.year, 1, 1), today


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_transactions(
    engine,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> pd.DataFrame:
    """Load transactions between `since` and `until` (inclusive on both
    ends) into a DataFrame. Defaults preserve the previous behaviour:
    `since` = 12 months ago, `until` = today.

    Returns an empty DataFrame with the canonical schema when there are
    no rows. Query is delegated to TransactionService to keep `ui/` out
    of `db/` (coupling rule)."""
    since = since or _twelve_months_ago()
    rows = TransactionService(engine).get_recent_for_home(since.isoformat())
    if not rows:
        return pd.DataFrame(columns=["date", "amount", "tx_type", "category", "subcategory", "reconciled"])

    df = pd.DataFrame(rows, columns=["date", "amount", "tx_type", "category", "subcategory", "reconciled"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = df["amount"].apply(lambda v: float(v) if v is not None else 0.0)
    df["reconciled"] = df["reconciled"].astype(bool)
    # Apply the upper bound after the cheap service query (it only filters
    # by `>= since`, by design — we'd rather post-filter in pandas than
    # widen the service contract for a UI-specific need).
    if until is not None:
        df = df[df["date"] <= pd.Timestamp(until)]
    return df


# ── Empty state ──────────────────────────────────────────────────────────────

def _render_empty_state() -> None:
    st.info(_t("home.empty_state.message"))
    if st.button(_t("home.empty_state.cta"), key="_home_empty_cta"):
        st.session_state["page"] = "import"
        st.rerun()


# ── Refunds ──────────────────────────────────────────────────────────────────

def _split_flows(
    df: pd.DataFrame, expense_categories: Optional[set[str]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Income rows and expense rows of *df*: every movement of the period
    except the giroconti (NOT_COUNTED_TYPES), the same as the Registry totals.
    Card movements count like any other.

    An income movement whose category is an expense category is a refund (a
    return, money back on a purchase): it lowers that expense instead of
    counting as income. The expense frame carries ``abs_amount``, the spent
    amount as a positive number, negative for a refund.
    """
    if df.empty:
        none = pd.DataFrame(columns=["date", "amount", "category", "subcategory", "abs_amount"])
        return none, none
    live = df[~df["tx_type"].isin(NOT_COUNTED_TYPES)]
    inc = live[live["amount"] > 0].copy()
    exp = live[live["amount"] < 0].copy()
    exp["abs_amount"] = exp["amount"].abs()
    if expense_categories:
        refund = inc["category"].isin(expense_categories)
        refunds = inc[refund].copy()
        refunds["abs_amount"] = -refunds["amount"]
        inc = inc[~refund]
        exp = pd.concat([exp, refunds], ignore_index=True)
    return inc, exp


# ── Chart 1: Bar: income above zero, expenses below, by month ───────────────

_TOP_CATEGORIES = 8


def _monthly_by_category(frame: pd.DataFrame, value: str, other: str, top: int) -> pd.DataFrame:
    """Months x categories, the largest *top* categories kept, the rest in
    *other*. A category whose month nets below zero (refunds larger than the
    spending) counts zero: a bar never crosses to the other side."""
    if frame.empty:
        return pd.DataFrame()
    f = frame.copy()
    f["month"] = f["date"].dt.to_period("M").dt.to_timestamp()
    pivot = f.groupby(["month", "category"])[value].sum().unstack(fill_value=0.0).clip(lower=0.0)
    order = pivot.sum().sort_values(ascending=False)
    order = order[order > 0]
    keep = list(order.index[:top])
    rest = [c for c in order.index if c not in keep]
    out = pivot[keep].copy()
    if rest:
        out[other] = pivot[rest].sum(axis=1)
    return out.sort_index()


def _build_monthly_flow(
    df: pd.DataFrame, expense_categories: Optional[set[str]] = None, top: int = _TOP_CATEGORIES,
) -> Optional[dict]:
    """Data for the monthly chart: ``income`` and ``expense`` are months x
    categories (positive amounts), on the same months. None when the period
    has neither."""
    inc, exp = _split_flows(df, expense_categories)
    uncat = _t("home.charts.uncategorised")
    other = _t("home.charts.other")
    inc = inc.assign(category=inc["category"].fillna(uncat))
    exp = exp.assign(category=exp["category"].fillna(uncat))
    income = _monthly_by_category(inc, "amount", other, top)
    expense = _monthly_by_category(exp, "abs_amount", other, top)
    if income.empty and expense.empty:
        return None
    months = income.index.union(expense.index)
    return {
        "income": income.reindex(months, fill_value=0.0),
        "expense": expense.reindex(months, fill_value=0.0),
    }


def _render_monthly_flow(data: dict, palette) -> None:
    """Each category in its own colour (spec 25): the same in every month and
    every chart; "other" and the uncategorised in grey."""
    import plotly.graph_objects as go

    inc_name, exp_name = _t("home.charts.income_side"), _t("home.charts.expense_side")
    fig = go.Figure()

    def add(frame: pd.DataFrame, direction: str, side: str, sign: float) -> None:
        for cat in frame.columns:
            values = frame[cat].values
            fig.add_bar(
                name=str(cat),
                x=frame.index,
                y=sign * values,
                customdata=values,
                marker_color=palette.category(cat, direction),
                legendgroup=side,
                legendgrouptitle_text=side,
                hovertemplate=f"{side} · %{{fullData.name}}<br>%{{x|%m/%Y}}: %{{customdata:,.0f}}<extra></extra>",
            )

    add(data["income"], "income", inc_name, 1.0)
    add(data["expense"], "expense", exp_name, -1.0)
    fig.update_layout(
        barmode="relative",
        title=_t("home.charts.flow_title"),
        xaxis_title=_t("home.charts.month"),
        yaxis_title=_t("home.charts.amount"),
        legend=dict(groupclick="toggleitem"),
        height=480,
        margin=dict(l=40, r=40, t=60, b=40),
    )
    fig.add_hline(y=0, line_width=1, line_color=palette.tokens["other"])
    st.plotly_chart(fig, width="stretch")


# ── Chart 2: Sankey — income → expense category → subcategory ────────────────

def _build_sankey_data(
    df: pd.DataFrame, expense_categories: Optional[set[str]] = None,
) -> Optional[dict]:
    """Build node/link arrays for the Sankey. Returns None if there is no
    income AND no expense (chart pointless), otherwise dict with keys:
        nodes: list[str]
        link_source: list[int]
        link_target: list[int]
        link_value:  list[float]
        unknown_kind: 'surplus' | 'deficit' | 'parity'  (informational)
    """
    # Refunds lower their expense category, as in the monthly chart.
    inc_df, exp_df = _split_flows(df, expense_categories)

    total_income = float(inc_df["amount"].sum())

    if total_income == 0.0 and exp_df.empty:
        return None

    # ── Node layout ──────────────────────────────────────────────────────────
    income_label = _t("home.charts.income_total")
    unknown_label = _t("home.charts.unknown")
    uncat_cat_label = _t("home.charts.uncategorised")
    uncat_sub_label = _t("home.charts.uncategorised_sub")

    nodes: list[str] = [income_label]  # 0 = income source
    node_idx: dict[str, int] = {income_label: 0}
    # What each node is, for its colour (spec 25): ("income",),
    # ("category", cat), ("subcategory", cat, sub) or ("unknown",).
    node_kinds: list[tuple] = [("income",)]

    def _node(label: str, kind: tuple) -> int:
        if label not in node_idx:
            node_idx[label] = len(nodes)
            nodes.append(label)
            node_kinds.append(kind)
        return node_idx[label]

    link_source: list[int] = []
    link_target: list[int] = []
    link_value: list[float] = []

    # Expense category aggregation (with "uncategorised" fallback).
    exp_df["category"] = exp_df["category"].fillna(uncat_cat_label)
    exp_df["subcategory"] = exp_df["subcategory"].fillna(uncat_sub_label)
    cat_totals = exp_df.groupby("category")["abs_amount"].sum().to_dict()
    # A category whose refunds exceed its spending draws nothing.
    total_expense = float(sum(v for v in cat_totals.values() if v > 0))

    # AI-119: order matters — Plotly Sankey with arrangement="perpendicular"
    # lays out nodes within each column in the order they're added to the
    # `nodes` list. Sort categories by value DESC so the biggest is on top.
    for cat, amount in sorted(cat_totals.items(), key=lambda kv: -kv[1]):
        if amount <= 0:
            continue
        cat_idx = _node(f"{cat}", ("category", cat))
        link_source.append(0)
        link_target.append(cat_idx)
        link_value.append(amount)

    # AI-119: subcategories are grouped by their parent (so siblings stay
    # adjacent), and within each group sorted by value DESC. Two-key sort
    # on (cat_total DESC, abs_amount DESC) achieves both at once.
    subcat_totals = (
        exp_df.groupby(["category", "subcategory"])["abs_amount"].sum().reset_index()
    )
    subcat_totals["cat_total"] = subcat_totals["category"].map(cat_totals)
    subcat_totals = subcat_totals.sort_values(
        by=["cat_total", "abs_amount"],
        ascending=[False, False],
    )
    for _, row in subcat_totals.iterrows():
        amount = float(row["abs_amount"])
        if amount <= 0:
            continue
        # A category whose refunds exceed its spending draws nothing, its
        # subcategories included: otherwise it shows as a source of money.
        if cat_totals.get(row["category"], 0) <= 0:
            continue
        cat_idx = _node(f"{row['category']}", ("category", row["category"]))
        sub_idx = _node(f"{row['subcategory']} ({row['category']})",
                        ("subcategory", row["category"], row["subcategory"]))
        link_source.append(cat_idx)
        link_target.append(sub_idx)
        link_value.append(amount)

    # ── Unknown node: surplus → target, deficit → source ────────────────────
    delta = total_income - total_expense
    if delta > 0:
        # Surplus: income flows partly to Unknown.
        unknown_idx = _node(unknown_label, ("unknown",))
        link_source.append(0)
        link_target.append(unknown_idx)
        link_value.append(delta)
        unknown_kind = "surplus"
    elif delta < 0:
        # Deficit: Unknown supplies the missing budget.
        unknown_idx = _node(unknown_label, ("unknown",))
        # Each category currently gets its abs amount from income (0). Re-balance:
        # Unknown is an extra source; we model it as a separate flow that goes
        # collectively to a virtual "expense pool". Simplest: Unknown → income_label
        # is unsound; Sankey needs Unknown → each category for the deficit slice.
        # We instead add a synthetic flow Unknown → income_label so the balance
        # math reads cleanly (the total entering "income" equals total expenses).
        link_source.append(unknown_idx)
        link_target.append(0)
        link_value.append(-delta)
        unknown_kind = "deficit"
    else:
        unknown_kind = "parity"

    return {
        "nodes": nodes,
        "node_kinds": node_kinds,
        "link_source": link_source,
        "link_target": link_target,
        "link_value": link_value,
        "unknown_kind": unknown_kind,
    }


def _node_color(kind: tuple, palette) -> str:
    if kind[0] == "income":
        return palette.tokens["income"]
    if kind[0] == "category":
        return palette.category(kind[1], "expense")
    if kind[0] == "subcategory":
        return palette.subcategory(kind[1], kind[2], "expense")
    return palette.tokens["other"]


def _transparent(color: str, alpha: float = 0.35) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _render_sankey(data: dict, palette) -> None:
    import plotly.graph_objects as go

    node_colors = [_node_color(k, palette) for k in data["node_kinds"]]
    # A link takes the colour of where the money goes (spec 25).
    link_colors = [_transparent(node_colors[t]) for t in data["link_target"]]

    # AI-118: dynamic height + larger pad to keep small branches readable.
    # Plotly Sankey sizes branches proportionally to value, so tiny categories
    # become unreadable. We can't enforce a true min-height, but giving the
    # chart more vertical space + node padding makes the labels survive.
    # The densest column drives the layout: roughly len(nodes)//3 (income,
    # expense_cat, expense_subcat). Each node wants ~22 px of breathing room.
    n_nodes = len(data["nodes"])
    densest_col = max(1, n_nodes // 3)
    chart_height = max(600, min(1400, densest_col * 22))

    fig = go.Figure(
        go.Sankey(
            arrangement="perpendicular",
            node=dict(
                label=data["nodes"],
                color=node_colors,
                pad=22, thickness=18,
            ),
            link=dict(
                source=data["link_source"],
                target=data["link_target"],
                value=data["link_value"],
                color=link_colors,
            ),
        )
    )
    fig.update_layout(
        title=_t("home.charts.sankey_title"),
        height=chart_height,
        margin=dict(l=10, r=10, t=60, b=10),
    )
    st.plotly_chart(fig, width="stretch")


# ── Entry point ──────────────────────────────────────────────────────────────

def render_home_page(engine) -> None:
    st.title(_t("home.title"))

    if not TransactionService(engine).has_transactions():
        _render_empty_state()
        return

    today = date.today()

    # ── Period selector ─────────────────────────────────────────────────────
    # Default = the three months up to the last movement. Choice is sticky
    # across reruns via session_state. "Custom" reveals two date_input widgets.
    _SESS_PRESET = "_home_period_preset"
    _SESS_CUSTOM_FROM = "_home_period_custom_from"
    _SESS_CUSTOM_TO = "_home_period_custom_to"
    if _SESS_PRESET not in st.session_state:
        st.session_state[_SESS_PRESET] = _PRESET_LAST_LOADED
    if _SESS_CUSTOM_FROM not in st.session_state:
        st.session_state[_SESS_CUSTOM_FROM] = _twelve_months_ago(today)
    if _SESS_CUSTOM_TO not in st.session_state:
        st.session_state[_SESS_CUSTOM_TO] = today

    preset_labels = {p: _t(f"home.period.{p}") for p in PRESETS_ORDERED}
    selected_label = st.radio(
        _t("home.period.label"),
        options=list(preset_labels.values()),
        index=PRESETS_ORDERED.index(st.session_state[_SESS_PRESET]),
        horizontal=True,
        key="_home_period_radio",
    )
    # Reverse-lookup label → preset key.
    preset = next(k for k, lbl in preset_labels.items() if lbl == selected_label)
    st.session_state[_SESS_PRESET] = preset

    if preset == _PRESET_CUSTOM:
        col_from, col_to = st.columns(2)
        with col_from:
            st.session_state[_SESS_CUSTOM_FROM] = st.date_input(
                _t("home.period.from"),
                value=st.session_state[_SESS_CUSTOM_FROM],
                max_value=today,
                key="_home_period_from",
            )
        with col_to:
            st.session_state[_SESS_CUSTOM_TO] = st.date_input(
                _t("home.period.to"),
                value=st.session_state[_SESS_CUSTOM_TO],
                max_value=today,
                key="_home_period_to",
            )

    flow_svc = GuidedFlowService(engine)
    since, until = _resolve_period(
        preset,
        today=today,
        custom_from=st.session_state[_SESS_CUSTOM_FROM],
        custom_to=st.session_state[_SESS_CUSTOM_TO],
        landing=flow_svc.landing_period(),
    )

    stopped_at = flow_svc.stopped_at(today)
    if stopped_at is not None:
        st.info(_t("guided.stopped_at", date=stopped_at.strftime("%d/%m/%Y")))

    st.caption(_t("home.range_caption",
                  date_from=since.strftime("%d/%m/%Y"),
                  date_to=until.strftime("%d/%m/%Y")))
    st.divider()

    df = _load_transactions(engine, since=since, until=until)

    if df.empty:
        # has_transactions said True but the selected period is empty.
        st.info(_t("home.charts.no_income"))
        return

    settings = SettingsService(engine)
    expense_categories = set(settings.get_taxonomy().expenses)
    palette = settings.get_palette()
    flow = _build_monthly_flow(df, expense_categories)
    if flow is not None:
        _render_monthly_flow(flow, palette)
    sankey = _build_sankey_data(df, expense_categories)
    if sankey is not None:
        _render_sankey(sankey, palette)
