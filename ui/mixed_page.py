"""Mixed categories: the workflow step after the counterparts.

A counterpart whose movements sit in more than one category is one of two
things. Either the analysis engine was inconsistent (a pastry shop half
groceries, half bar): then one category is right for all of them and it
becomes a rule. Or it really depends on what was bought (the supermarket
that also sells electronics): rules match the counterpart's name, the same
on every movement, so no rule can tell them apart and the movements are
looked at one by one.

Movements the user has already seen are never asked again: a counterpart
worked through comes back only with what the last import added, and the
page says how much earlier work spared.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from services.guided_flow_service import GuidedFlowService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display, format_date_display
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.cat_select import build_cat_options, join_cat_sub, split_cat_sub

logger = setup_logging()

# The counterpart whose movements are open for a look one by one.
_OPEN = "_mixed_open"


def _split(categories: list[tuple[str, int]]) -> str:
    """'17 Alimentari · 16 Ristorazione'."""
    return " · ".join(f"{n} {cat}" for cat, n in categories)


def _always(desc: str, cat_sub: str, tx_svc: TransactionService, rule_svc: RuleService) -> None:
    """One category for every movement of the counterpart, past and future."""
    cat, sub = split_cat_sub(cat_sub)
    rule_svc.create_rule(pattern=desc, match_type="exact", category=cat, subcategory=sub, priority=10)
    txs = tx_svc.get_by_rule_pattern(desc, "exact")
    for tx in txs:
        tx_svc.update_category(tx.id, cat, sub, origin="mixed")
    logger.info(f"mixed_page: rule pattern={desc!r} -> {cat!r}/{sub!r}, {len(txs)} movements")


def _render_movements(
    desc: str,
    flow_svc: GuidedFlowService,
    tx_svc: TransactionService,
    cat_options: list[str],
    date_fmt: str,
    fmt,
) -> None:
    """The unseen movements of one counterpart, each with its own category."""
    txs = flow_svc.unchecked_movements(desc)
    col_date = t("mixed.col_date")
    col_amount = t("mixed.col_amount")
    col_cat = t("mixed.col_category")
    df = pd.DataFrame([
        {
            col_date: format_date_display(tx.date, date_fmt),
            col_amount: fmt(float(tx.amount or 0)),
            col_cat: join_cat_sub(tx.category, tx.subcategory),
            "_id": tx.id,
        }
        for tx in txs
    ])
    edited = st.data_editor(
        df[[col_date, col_amount, col_cat]],
        column_config={
            col_date: st.column_config.TextColumn(col_date, disabled=True, width="small"),
            col_amount: st.column_config.TextColumn(col_amount, disabled=True, width="small"),
            col_cat: st.column_config.SelectboxColumn(col_cat, options=cat_options, width="large"),
        },
        hide_index=True,
        num_rows="fixed",
        width="stretch",
        key=f"mx_editor_{desc}",
    )
    if st.button(t("mixed.done_btn"), type="primary", key=f"mx_done_{desc}"):
        # Every movement shown counts as seen, also the ones left as they were:
        # the next round shows only what a later import adds.
        for idx in range(len(df)):
            cat, sub = split_cat_sub(edited.at[idx, col_cat] or "")
            if cat:
                tx_svc.update_category(df.at[idx, "_id"], cat, sub, origin="mixed")
            else:
                tx_svc.validate(df.at[idx, "_id"])
        st.session_state.pop(_OPEN, None)
        st.rerun()


def render_mixed_page(engine) -> None:
    st.header(t("mixed.title"))

    flow_svc = GuidedFlowService(engine)
    tx_svc = TransactionService(engine)
    rule_svc = RuleService(engine)
    cfg_svc = SettingsService(engine)

    settings = cfg_svc.get_all()
    dec = settings.get("amount_decimal_sep", ",")
    thou = settings.get("amount_thousands_sep", ".")
    date_fmt = settings.get("date_display_format", "%d/%m/%Y")

    def fmt(v: float) -> str:
        return format_amount_display(v, decimal_sep=dec, thousands_sep=thou)

    items = flow_svc.mixed_counterparts()
    if not items:
        st.success(t("mixed.none"))
        return

    st.markdown(t("mixed.intro"))
    st.caption(t("mixed.optional"))
    cat_options = build_cat_options(cfg_svc.get_taxonomy(), include_empty=True)

    for s in items:
        desc = s["description"]
        with st.container(border=True):
            st.markdown(f"**{desc}** · {fmt(s['total_amount'])}")
            st.caption(t("mixed.split", split=_split(s["categories"])))
            done = s["tx_count"] - s["unchecked_count"]
            if done > 0:
                st.caption(t("mixed.only_new", done=done, todo=s["unchecked_count"]))

            modal = join_cat_sub(s["modal_category"], s["modal_subcategory"])
            col_cat, col_always, col_each = st.columns([3, 2, 2], vertical_alignment="bottom")
            choice = col_cat.selectbox(
                t("mixed.always_label"), cat_options,
                index=cat_options.index(modal) if modal in cat_options else 0,
                key=f"mx_cat_{desc}",
            )
            if col_always.button(t("mixed.always_btn"), key=f"mx_always_{desc}", disabled=not choice):
                _always(desc, choice, tx_svc, rule_svc)
                st.rerun()
            if col_each.button(t("mixed.each_btn"), key=f"mx_each_{desc}"):
                st.session_state[_OPEN] = desc
                st.rerun()

            if st.session_state.get(_OPEN) == desc:
                _render_movements(desc, flow_svc, tx_svc, cat_options, date_fmt, fmt)
