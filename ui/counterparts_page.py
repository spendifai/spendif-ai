"""Counterparts page: per-counterpart stats grid with inline rule creation."""
from __future__ import annotations

import math

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
from ui.widgets.cat_select import build_cat_options, join_cat_sub, split_cat_sub, wrong_direction
from ui.widgets.immersive import GUIDED_PAGE
from ui.widgets.written_rules import remember, rewrite_pending, show_written_rules, write_and_apply

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
                t("counterparts.col_multi"): s.get("is_multi_category", False),
                t("counterparts.col_source"): src_label,
                t("counterparts.col_variability"): f"{var:.0f}%",
                t("counterparts.col_categorized"): s["categorized_by_user"],
                "_description": s["description"],
                "_orig_cat_sub": combined,
                "_orig_giroconto": s["is_giroconto"],
                "_orig_multi": s.get("is_multi_category", False),
                "_orig_categorized": s["categorized_by_user"],
                "_direction": s.get("direction"),
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


def _apply_to_income(tx_svc: TransactionService, r: dict) -> int:
    """A row of the income step for a counterpart that also has spending:
    the choice goes to its income movements only, one by one, no rule."""
    txs = [tx for tx in tx_svc.get_by_rule_pattern(r["description"], "exact")
           if tx.tx_type == "income" or (tx.tx_type == "card_tx" and float(tx.amount or 0) > 0)]
    for tx in txs:
        if r["giroconto"]:
            tx_svc.toggle_giroconto(tx.id)
        elif r["set_category"]:
            tx_svc.update_category(tx.id, r["new_cat"], r["new_sub"], origin="income")
    logger.info(f"counterparts_page: {len(txs)} income movements set one by one")
    return len(txs)


def _render_guided_intro(flow_svc: GuidedFlowService, fmt) -> None:
    """Criterion and progress of the workflow step, in movements and in words.

    The goal is a suggestion: Next works at any point, and the sentence
    says so, also before the goal is reached.
    """
    period = flow_svc.workflow_period()
    progress = flow_svc.check_progress()
    if period is None or progress.total <= 0:
        st.info(t("counterparts.guided.no_spending"))
        return
    st.markdown(t(
        "counterparts.guided.criterion",
        date_from=_fmt_date(period[0]), date_to=_fmt_date(period[1]),
    ))
    # What earlier ticks already spared: movements the rules categorized alone.
    n_saved, saved = flow_svc.rule_categorized_spending()
    if n_saved:
        st.caption(t("counterparts.guided.saved", n=n_saved, amount=fmt(saved)))
    # What the step does not ask, and why: never a silent filter.
    small = flow_svc.small_singles()
    if small.count:
        st.caption(t("counterparts.guided.small_singles", n=small.count, amount=fmt(small.total),
                     limit=fmt(small.limit), cap=f"{flow_svc.small_singles_share * 100:g}",
                     years=flow_svc.small_singles_years))
    # Counted in movements, as the list is ordered (most movements first).
    counts = {"checked": int(progress.checked), "total": int(progress.total)}
    if progress.reached:
        st.success(t("counterparts.guided.reached", **counts))
        return
    key = "counterparts.guided.progress_one" if progress.needed == 1 else "counterparts.guided.progress"
    st.info(t(key, missing=math.ceil(progress.missing), needed=progress.needed, **counts))
    st.caption(t("counterparts.guided.optional"))


def _render_income_intro(flow_svc: GuidedFlowService) -> None:
    """What the income step asks, and how a refund is told apart."""
    period = flow_svc.workflow_period()
    st.markdown(t(
        "income.guided.criterion",
        date_from=_fmt_date(period[0]), date_to=_fmt_date(period[1]),
    ))
    st.caption(t("income.guided.refunds"))


def render_income_page(engine) -> None:
    """Income step of the workflow: the income counterparts not seen yet.

    No goal: income counterparts are few and each one weighs, so all of
    them are shown. A counterpart with spending too (``also_spending``) gets
    its category on its income movements only, without a rule.
    """
    st.markdown(_DROPDOWN_CSS, unsafe_allow_html=True)
    st.header(t("income.title"))
    flow_svc = GuidedFlowService(engine)
    if flow_svc.workflow_period() is None or not flow_svc.income_counterparts():
        st.info(t("income.guided.none"))
        return
    _render_income_intro(flow_svc)
    filtered = flow_svc.uncategorized_income()
    if not filtered:
        st.success(t("income.guided.all_checked"))
        return
    cfg_svc = SettingsService(engine)
    taxonomy = cfg_svc.get_taxonomy()
    cat_options = build_cat_options(taxonomy, include_empty=True, only="income")
    one_by_one = {s["description"] for s in filtered if s["also_spending"]}
    if one_by_one:
        st.caption(t("income.guided.one_by_one", names=", ".join(sorted(one_by_one))))
    _render_grid(
        filtered, cat_options, TransactionService(engine), RuleService(engine),
        guided=True, one_by_one=one_by_one, with_multi=False, taxonomy=taxonomy,
    )


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
    rewrite_pending(rule_svc, tx_svc)

    taxonomy = cfg_svc.get_taxonomy()

    if guided:
        settings = cfg_svc.get_all()
        dec = settings.get("amount_decimal_sep", ",")
        thou = settings.get("amount_thousands_sep", ".")
        flow_svc = GuidedFlowService(engine)
        _render_guided_intro(
            flow_svc, lambda v: format_amount_display(v, decimal_sep=dec, thousands_sep=thou),
        )
        # The counterparts seen once for a small amount, on the user's choice.
        small = flow_svc.small_singles()
        show_small = False
        if small.count:
            pct = f"{small.share * 100:.1f}".replace(".", dec)
            show_small = st.toggle(
                t("counterparts.guided.show_small", n=small.count, share=pct,
                  limit=format_amount_display(small.limit, decimal_sep=dec, thousands_sep=thou)),
                value=False, key="cp_show_small",
            )
        filtered = [s for s in flow_svc.expense_counterparts(include_small=show_small)
                    if not s["categorized_by_user"]]
        if not filtered:
            if flow_svc.check_progress().total > 0:
                st.success(t("counterparts.guided.all_categorized"))
            show_written_rules()
            return
    else:
        filtered = _filtered_stats(tx_svc)
        if filtered is None:
            show_written_rules()
            return
    # A search box on the counterpart and its category; it narrows what is
    # shown, never the goal of the step.
    query = st.text_input(t("counterparts.search"), key="cp_search",
                          placeholder=t("counterparts.search_placeholder"))
    if query.strip():
        filtered = [s for s in filtered
                    if tx_svc.text_matches(query, s["description"], s.get("modal_category"),
                                           s.get("modal_subcategory"))]
        if not filtered:
            st.info(t("counterparts.search_none", query=query.strip()))
            return
    # One menu for the column: the directions of the counterparts shown (the
    # spending step shows spending only); each row is checked at the save.
    _sides = {s.get("direction") for s in filtered}
    _only = _sides.pop() if len(_sides) == 1 and None not in _sides else None
    cat_options = build_cat_options(taxonomy, include_empty=True,
                                    only=_only if _only in ("expense", "income") else None)
    _render_grid(filtered, cat_options, tx_svc, rule_svc, guided, taxonomy=taxonomy)


def _filtered_stats(tx_svc: TransactionService) -> list[dict] | None:
    """Regular page: every counterpart, with the user's filters and sort."""
    # Giroconti too, so a counterpart marked as one stays here and can be
    # unmarked; card movements too, where the merchants of a card statement are.
    stats = tx_svc.get_counterpart_stats(
        tx_types=("expense", "income", "card_tx", "internal_out", "internal_in"),
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
        filter_uncategorized = st.toggle(
            t("counterparts.filter_uncategorized"), value=False, key="cp_filter_unc"
        )

    # ── Apply filters & sort ──────────────────────────────────────────────────
    filtered = stats
    if filter_source is not None:
        filtered = [s for s in filtered if s["source_mode"] == filter_source]
    if filter_low_var:
        filtered = [s for s in filtered if s["variability_pct"] < _VARIABILITY_WARN]
    if filter_uncategorized:
        filtered = [s for s in filtered if not s["categorized_by_user"]]
    filtered.sort(key=lambda s: s[sort_key], reverse=not sort_asc)
    return filtered


def _render_grid(
    filtered: list[dict],
    cat_options: list[str],
    tx_svc: TransactionService,
    rule_svc: RuleService,
    guided: bool,
    one_by_one: set[str] | None = None,
    with_multi: bool = True,
    taxonomy=None,
) -> None:
    """*one_by_one*: counterparts whose income movements get the category
    one by one, without a rule (the income step, for a counterpart that
    also has spending a rule would change). *with_multi*: the column that
    marks a place where everything is bought (spec 24), for spending."""
    one_by_one = one_by_one or set()
    df = _build_df(filtered, cat_options)

    _col_counterpart = t("counterparts.col_counterpart")
    _col_tx          = t("counterparts.col_tx_count")
    _col_avg         = t("counterparts.col_avg_amount")
    _col_total       = t("counterparts.col_total")
    _col_cat_sub     = t("counterparts.col_cat_sub")
    _col_giro        = t("counterparts.col_giroconto")
    _col_multi       = t("counterparts.col_multi")
    _col_src         = t("counterparts.col_source")
    _col_var         = t("counterparts.col_variability")
    _col_cat_by_you         = t("counterparts.col_categorized")

    if guided:
        display_cols = [
            _col_counterpart, _col_tx, _col_total,
            _col_cat_sub, _col_giro, _col_multi, _col_cat_by_you,
        ]
    else:
        display_cols = [
            _col_counterpart, _col_tx, _col_avg,
            _col_cat_sub, _col_giro, _col_multi,
            _col_src, _col_var, _col_cat_by_you,
        ]
    if not with_multi:
        display_cols.remove(_col_multi)

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
        _col_multi: st.column_config.CheckboxColumn(
            _col_multi, help=t("counterparts.col_multi_help"), width="small"
        ),
        _col_src: st.column_config.TextColumn(
            _col_src, disabled=True, width="small"
        ),
        _col_var: st.column_config.TextColumn(
            _col_var, help=t("counterparts.col_variability_help"), disabled=True, width="small"
        ),
        # Ticking it says "always like this": the category shown becomes a
        # rule for every movement of the counterpart, past and future.
        _col_cat_by_you: st.column_config.CheckboxColumn(
            t("counterparts.col_always") if guided else _col_cat_by_you,
            help=t("counterparts.col_categorized_help"), width="small",
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
    # wins over a category picked in the same row. Ticking "category set by
    # you" keeps the category shown and makes it the rule.
    changed_rows = []
    for idx in range(len(df)):
        orig = df.at[idx, "_orig_cat_sub"]
        new_val = edited.at[idx, _col_cat_sub]
        orig_giro = bool(df.at[idx, "_orig_giroconto"])
        new_giro = bool(edited.at[idx, _col_giro])
        orig_multi = bool(df.at[idx, "_orig_multi"])
        new_multi = bool(edited.at[idx, _col_multi]) if with_multi else orig_multi
        orig_cat_by_you = bool(df.at[idx, "_orig_categorized"])
        new_cat_by_you = bool(edited.at[idx, _col_cat_by_you])
        cat_changed = bool(new_val) and new_val != orig
        confirm = new_cat_by_you and not orig_cat_by_you and not new_giro and not new_multi
        if orig_cat_by_you and not new_cat_by_you and not (orig_multi and not new_multi):
            st.caption(t("counterparts.uncheck_ignored", name=df.at[idx, "_description"]))
        if confirm and not new_val:
            st.warning(t("counterparts.confirm_no_category", name=df.at[idx, "_description"]))
            confirm = False
        if new_giro == orig_giro and new_multi == orig_multi and not cat_changed and not confirm:
            continue
        new_cat, new_sub = split_cat_sub(new_val) if new_val else ("", "")
        _dir = df.at[idx, "_direction"]
        if (cat_changed and taxonomy is not None and _dir in ("expense", "income")
                and wrong_direction(taxonomy, new_cat, -1 if _dir == "expense" else 1)):
            st.error(t("counterparts.wrong_direction", name=df.at[idx, "_description"], cat=new_cat))
            continue
        changed_rows.append(
            {
                "description": df.at[idx, "_description"],
                "giroconto": new_giro,
                "unmark_giroconto": orig_giro and not new_giro,
                "new_cat": new_cat,
                "new_sub": new_sub,
                # A multi-category counterpart has no category (spec 24).
                "multi": new_multi and not new_giro,
                "multi_changed": new_multi != orig_multi and not new_giro,
                "set_category": (cat_changed or confirm) and not new_giro and not new_multi,
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
            # The product writes the category rules (spec 18): merchant name
            # and "starts with" when it can tell the name, else the exact text.
            # The model may take seconds: say so, or the user clicks again.
            with st.spinner(t("counterparts.writing_rules")):
                written = rule_svc.write_rules(
                    [r["description"] for r in changed_rows
                     if r["set_category"] and r["description"] not in one_by_one],
                    queue=True,
                )
            # From here to the end of the writes no Streamlit command: a click
            # would stop the run at the next one, between two rules.
            notes: list[str] = []
            for r in changed_rows:
                if r["description"] in one_by_one:
                    n = _apply_to_income(tx_svc, r)
                    saved += 1 if n else 0
                    applied += n
                    continue
                if r["multi_changed"]:
                    # A place where everything is bought, or no longer (spec 24).
                    applied += rule_svc.set_multi_category(r["description"], r["multi"])
                    saved += 1
                    logger.info(f"counterparts_page: multi-category {r['multi']}")
                    continue
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
                        f"counterparts_page: {'created' if created else 'updated'} giroconto rule"
                    )
                elif r["set_category"]:
                    pattern, match_type = written[r["description"]]
                    # A tick covers the movements already there, or it would
                    # not leave the counterpart categorized by the user.
                    n, note = write_and_apply(
                        rule_svc, tx_svc, r["description"], pattern, match_type,
                        r["new_cat"], r["new_sub"], origin="counterparts",
                        apply=retroapply or r["confirm"],
                    )
                    applied += n
                    notes.append(note)
                    saved += 1
                    logger.info(
                        f"counterparts_page: rule {match_type}"
                        f" → {r['new_cat']!r}/{r['new_sub']!r}"
                    )
                elif r["unmark_giroconto"]:
                    # No category chosen: the giroconto rule just goes away.
                    if rule_svc.delete_giroconto_rule(r["description"], "exact"):
                        saved += 1
                        logger.info(
                            "counterparts_page: deleted giroconto rule"
                        )
                # Giroconto rules stay exact: their descriptions are often
                # people's names (spec 18).
                if (retroapply or r["confirm"]) and (r["giroconto"] or r["unmark_giroconto"]):
                    applied += tx_svc.set_giroconto_by_rule_pattern(
                        r["description"], "exact", make_giroconto=r["giroconto"],
                    )

            msg = t("counterparts.saved_ok", n=saved)
            if applied:
                msg += " " + t("counterparts.retroapplied", n=applied)
            # Shown after the rerun: a message written here would vanish with it.
            remember(notes, msg)
            st.rerun()
    else:
        st.caption(t("counterparts.no_changes"))
    # What the last save wrote, under the button and its progress message,
    # where the eye is after the click (founder, 2026-10-21).
    show_written_rules()
