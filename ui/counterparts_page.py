"""Counterparts page: per-counterpart stats grid with inline rule creation."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from services.guided_flow_service import GuidedFlowService
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.checklist_page import _fmt_date
from ui.i18n import t
from ui.widgets.cat_select import build_cat_options, join_cat_sub, split_cat_sub
from ui.widgets.immersive import GUIDED_PAGE

logger = setup_logging()

# Who chose the category, in the user's words (the stored value is internal).
_SOURCE_LABEL_KEY = {
    "llm": "counterparts.filter_source_llm",
    "rule": "counterparts.filter_source_rule",
    "manual": "counterparts.filter_source_manual",
    "mixed": "counterparts.filter_source_mixed",
    "unknown": "counterparts.source_unknown",
}

_VARIABILITY_WARN = 80.0


def _build_df(stats: list[dict], cat_options: list[str]) -> pd.DataFrame:
    rows = []
    for s in stats:
        src = s["source_mode"]
        src_label = t(_SOURCE_LABEL_KEY.get(src, "counterparts.source_unknown"))
        var = s["variability_pct"]
        combined = join_cat_sub(s["modal_category"], s["modal_subcategory"])
        rows.append(
            {
                t("counterparts.col_counterpart"): s["description"],
                t("counterparts.col_tx_count"): s["tx_count"],
                t("counterparts.col_avg_amount"): round(s["avg_amount"], 2),
                t("counterparts.col_total"): round(s["total_amount"], 2),
                t("counterparts.col_cat_sub"): combined,
                t("counterparts.col_giroconto"): s["is_giroconto"],
                t("counterparts.col_source"): src_label,
                t("counterparts.col_variability"): f"{var:.0f}%",
                t("counterparts.col_checked"): s["human_checked"],
                "_description": s["description"],
                "_orig_cat_sub": combined,
                "_orig_giroconto": s["is_giroconto"],
                "_orig_checked": s["human_checked"],
            }
        )
    return pd.DataFrame(rows)


_DROPDOWN_CSS = """
<style>
/* ag-grid SelectboxColumn dropdown popup */
.ag-rich-select {
    background-color: #16213e !important;
    border: 1px solid #53c28b !important;
    border-radius: 4px !important;
}
.ag-rich-select-list {
    background-color: #16213e !important;
}
.ag-rich-select-row {
    color: #e0e0e0 !important;
}
.ag-rich-select-row:hover,
.ag-rich-select-row.ag-hover {
    background-color: #1e3a5f !important;
    color: #ffffff !important;
}
.ag-rich-select-row.ag-rich-select-row-selected {
    background-color: #0f3460 !important;
    color: #53c28b !important;
}
</style>
"""


def _render_guided_intro(flow_svc: GuidedFlowService, fmt) -> None:
    """Criterion and progress of the workflow step, in money and in words.

    The goal is a suggestion: Next works at any point, and the sentence
    says so, also before the goal is reached.
    """
    period = flow_svc.landing_period()
    progress = flow_svc.check_progress()
    if period is None or progress.total <= 0:
        st.info(t("counterparts.guided.no_spending"))
        return
    st.markdown(t(
        "counterparts.guided.criterion",
        date_from=_fmt_date(period[0]), date_to=_fmt_date(period[1]),
    ))
    amounts = {"checked": fmt(progress.checked), "total": fmt(progress.total)}
    if progress.reached:
        st.success(t("counterparts.guided.reached", **amounts))
        return
    key = "counterparts.guided.progress_one" if progress.needed == 1 else "counterparts.guided.progress"
    st.info(t(key, missing=fmt(progress.missing), needed=progress.needed, **amounts))
    st.caption(t("counterparts.guided.optional"))


def render_counterparts_page(engine) -> None:
    st.markdown(_DROPDOWN_CSS, unsafe_allow_html=True)
    st.header(t("counterparts.title"))
    # Inside the workflow: no filters, only the spending still to check.
    guided = st.session_state.get("page") == GUIDED_PAGE
    if not guided:
        st.caption(t("counterparts.caption"))

    tx_svc = TransactionService(engine)
    rule_svc = RuleService(engine)
    cfg_svc = SettingsService(engine)

    taxonomy = cfg_svc.get_taxonomy()
    cat_options = build_cat_options(taxonomy, include_empty=True)

    if guided:
        settings = cfg_svc.get_all()
        dec = settings.get("amount_decimal_sep", ",")
        thou = settings.get("amount_thousands_sep", ".")
        flow_svc = GuidedFlowService(engine)
        _render_guided_intro(
            flow_svc, lambda v: format_amount_display(v, decimal_sep=dec, thousands_sep=thou),
        )
        filtered = [s for s in flow_svc.expense_counterparts() if not s["human_checked"]]
        if not filtered:
            if flow_svc.check_progress().total > 0:
                st.success(t("counterparts.guided.all_checked"))
            return
    else:
        filtered = _filtered_stats(tx_svc)
        if filtered is None:
            return
    _render_grid(filtered, cat_options, tx_svc, rule_svc, guided)


def _filtered_stats(tx_svc: TransactionService) -> list[dict] | None:
    """Regular page: every counterpart, with the user's filters and sort."""
    # Giroconti too, so a counterpart marked as one stays here and can be unmarked.
    stats = tx_svc.get_counterpart_stats(
        tx_types=("expense", "income", "internal_out", "internal_in"),
    )
    if not stats:
        st.info(t("counterparts.empty"))
        return None

    # ── Filters ───────────────────────────────────────────────────────────────
    f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1, 1])
    with f1:
        sort_opts = {
            t("counterparts.sort_tx_count"): "tx_count",
            t("counterparts.sort_avg_amount"): "avg_amount",
            t("counterparts.sort_variability"): "variability_pct",
            t("counterparts.sort_name"): "description",
        }
        sort_label = st.selectbox(
            t("counterparts.sort_by"), list(sort_opts.keys()), key="cp_sort"
        )
        sort_key = sort_opts[sort_label]
    with f2:
        sort_asc = st.toggle(t("counterparts.sort_asc"), value=False, key="cp_sort_asc")
    with f3:
        source_filter_opts = {
            t("counterparts.filter_source_all"): None,
            t("counterparts.filter_source_rule"): "rule",
            t("counterparts.filter_source_llm"): "llm",
            t("counterparts.filter_source_mixed"): "mixed",
            t("counterparts.filter_source_manual"): "manual",
        }
        source_label = st.selectbox(
            t("counterparts.filter_source"), list(source_filter_opts.keys()), key="cp_filter_src"
        )
        filter_source = source_filter_opts[source_label]
    with f4:
        filter_low_var = st.toggle(
            t("counterparts.filter_low_var"), value=False, key="cp_filter_var"
        )
    with f5:
        filter_unchecked = st.toggle(
            t("counterparts.filter_unchecked"), value=False, key="cp_filter_unc"
        )

    # ── Apply filters & sort ──────────────────────────────────────────────────
    filtered = stats
    if filter_source is not None:
        filtered = [s for s in filtered if s["source_mode"] == filter_source]
    if filter_low_var:
        filtered = [s for s in filtered if s["variability_pct"] < _VARIABILITY_WARN]
    if filter_unchecked:
        filtered = [s for s in filtered if not s["human_checked"]]
    filtered.sort(key=lambda s: s[sort_key], reverse=not sort_asc)
    return filtered


def _render_grid(
    filtered: list[dict],
    cat_options: list[str],
    tx_svc: TransactionService,
    rule_svc: RuleService,
    guided: bool,
) -> None:
    df = _build_df(filtered, cat_options)

    _col_counterpart = t("counterparts.col_counterpart")
    _col_tx          = t("counterparts.col_tx_count")
    _col_avg         = t("counterparts.col_avg_amount")
    _col_total       = t("counterparts.col_total")
    _col_cat_sub     = t("counterparts.col_cat_sub")
    _col_giro        = t("counterparts.col_giroconto")
    _col_src         = t("counterparts.col_source")
    _col_var         = t("counterparts.col_variability")
    _col_chk         = t("counterparts.col_checked")

    if guided:
        display_cols = [
            _col_counterpart, _col_tx, _col_total,
            _col_cat_sub, _col_giro, _col_chk,
        ]
    else:
        display_cols = [
            _col_counterpart, _col_tx, _col_avg,
            _col_cat_sub, _col_giro,
            _col_src, _col_var, _col_chk,
        ]

    column_config = {
        _col_counterpart: st.column_config.TextColumn(
            _col_counterpart, disabled=True, width="large"
        ),
        _col_tx: st.column_config.NumberColumn(
            _col_tx, disabled=True, width="small"
        ),
        _col_avg: st.column_config.NumberColumn(
            _col_avg, disabled=True, format="€ %.2f", width="small"
        ),
        _col_total: st.column_config.NumberColumn(
            _col_total, disabled=True, format="€ %.2f", width="small"
        ),
        _col_cat_sub: st.column_config.SelectboxColumn(
            _col_cat_sub,
            options=cat_options,
            required=False,
            width="large",
        ),
        _col_giro: st.column_config.CheckboxColumn(
            _col_giro, help=t("counterparts.col_giroconto_help"), width="small"
        ),
        _col_src: st.column_config.TextColumn(
            _col_src, disabled=True, width="small"
        ),
        _col_var: st.column_config.TextColumn(
            _col_var, help=t("counterparts.col_variability_help"), disabled=True, width="small"
        ),
        # Ticking it says "always like this": the category shown becomes a
        # rule for every movement of the counterpart, past and future.
        _col_chk: st.column_config.CheckboxColumn(
            t("counterparts.col_always") if guided else _col_chk,
            help=t("counterparts.col_checked_help"), width="small",
        ),
    }

    if not guided:
        st.caption(t("counterparts.grid_hint", n=len(filtered)))

    edited = st.data_editor(
        df[display_cols],
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        key="cp_editor",
        num_rows="fixed",
    )

    # ── Detect changes ────────────────────────────────────────────────────────
    # One rule per counterpart: giroconto, or a category. Ticking giroconto
    # wins over a category picked in the same row. Ticking "checked" keeps
    # the category shown and makes it the rule.
    changed_rows = []
    for idx in range(len(df)):
        orig = df.at[idx, "_orig_cat_sub"]
        new_val = edited.at[idx, _col_cat_sub]
        orig_giro = bool(df.at[idx, "_orig_giroconto"])
        new_giro = bool(edited.at[idx, _col_giro])
        orig_chk = bool(df.at[idx, "_orig_checked"])
        new_chk = bool(edited.at[idx, _col_chk])
        cat_changed = bool(new_val) and new_val != orig
        confirm = new_chk and not orig_chk and not new_giro
        if orig_chk and not new_chk:
            st.caption(t("counterparts.uncheck_ignored", name=df.at[idx, "_description"]))
        if confirm and not new_val:
            st.warning(t("counterparts.confirm_no_category", name=df.at[idx, "_description"]))
            confirm = False
        if new_giro == orig_giro and not cat_changed and not confirm:
            continue
        new_cat, new_sub = split_cat_sub(new_val) if new_val else ("", "")
        changed_rows.append(
            {
                "description": df.at[idx, "_description"],
                "giroconto": new_giro,
                "unmark_giroconto": orig_giro and not new_giro,
                "new_cat": new_cat,
                "new_sub": new_sub,
                "set_category": (cat_changed or confirm) and not new_giro,
                "confirm": confirm,
                "orig": orig,
            }
        )

    if changed_rows:
        st.info(t("counterparts.changes_pending", n=len(changed_rows)))

        n_affected = sum(
            len(tx_svc.get_by_rule_pattern(r["description"], "exact"))
            for r in changed_rows
        )
        # In the workflow every tick is "always like this": past movements too.
        retroapply = guided or st.checkbox(
            t("counterparts.retroapply", n=n_affected),
            value=True,
            key="cp_retroapply",
            disabled=n_affected == 0,
        )

        if st.button(t("counterparts.save_btn"), type="primary", key="cp_save"):
            saved = 0
            applied = 0
            for r in changed_rows:
                if r["giroconto"]:
                    _, created = rule_svc.create_rule(
                        pattern=r["description"],
                        match_type="exact",
                        category="",
                        subcategory="",
                        priority=10,
                        is_giroconto=True,
                    )
                    saved += 1
                    logger.info(
                        f"counterparts_page: {'created' if created else 'updated'} giroconto"
                        f" rule pattern={r['description']!r}"
                    )
                elif r["set_category"]:
                    _, created = rule_svc.create_rule(
                        pattern=r["description"],
                        match_type="exact",
                        category=r["new_cat"],
                        subcategory=r["new_sub"],
                        priority=10,
                    )
                    saved += 1
                    logger.info(
                        f"counterparts_page: {'created' if created else 'updated'} rule"
                        f" pattern={r['description']!r} → {r['new_cat']!r}/{r['new_sub']!r}"
                    )
                elif r["unmark_giroconto"]:
                    # No category chosen: the giroconto rule just goes away.
                    if rule_svc.delete_giroconto_rule(r["description"], "exact"):
                        saved += 1
                        logger.info(
                            f"counterparts_page: deleted giroconto rule"
                            f" pattern={r['description']!r}"
                        )
                # A tick covers the movements already there, or it would not
                # leave the counterpart checked.
                if retroapply or r["confirm"]:
                    if r["giroconto"] or r["unmark_giroconto"]:
                        applied += tx_svc.set_giroconto_by_rule_pattern(
                            r["description"], "exact", make_giroconto=r["giroconto"],
                        )
                    if r["set_category"]:
                        txs = tx_svc.get_by_rule_pattern(r["description"], "exact")
                        for tx in txs:
                            tx_svc.apply_rule_category(
                                tx.id, r["new_cat"], r["new_sub"], origin="counterparts"
                            )
                        applied += len(txs)

            msg = t("counterparts.saved_ok", n=saved)
            if applied:
                msg += " " + t("counterparts.retroapplied", n=applied)
            st.success(msg)
            st.rerun()
    else:
        st.caption(t("counterparts.no_changes"))
