"""Ledger page (RF-08): editable transaction table + export."""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from services.rule_service import STARTS_WITH, RuleService
from services.settings_service import SettingsService
from services.transaction_service import NOT_COUNTED_TYPES, TransactionService
from support.formatting import format_amount_display, format_date_display, strftime_to_momentjs
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.file_handoff import offer_file
from ui.widgets.cat_select import (
    build_cat_options, cat_sub_select, direction_of, join_cat_sub, options_for_amounts,
    split_cat_sub, wrong_direction,
)


# ── Colonne della tabella ────────────────────────────────────────────────────
# Chiavi INTERNE, stabili e indipendenti dalla lingua. L'etichetta visibile
# vive solo in column_config: prima queste due cose coincidevano, e con la UI
# in inglese il diff delle modifiche indicizzava colonne che non esistevano
# (KeyError al salvataggio) mentre la configurazione di alcune colonne veniva
# ignorata in silenzio.
COL_ID = "_id"
COL_SEL = "_sel"
COL_DATE = "date"
COL_DESC = "description"
COL_RAW = "raw"
COL_INCOME = "income"
COL_EXPENSE = "expense"
# Category and subcategory in one column, as in every grid of the app.
COL_CAT_SUB = "cat_sub"
COL_CONTEXT = "context"
COL_SOURCE = "source"
COL_FLAG_REVIEW = "flag_review"
COL_FLAG_VALID = "flag_validated"
COL_FLAG_TRANSFER = "flag_transfer"
COL_VALIDATED = "validated"
COL_TRANSFER = "transfer"

_INTERNAL_TRANSFER_TYPES = ("internal_out", "internal_in")


def build_ledger_row(tx, show_raw: bool, source_badge: dict) -> dict:
    """Una riga della tabella del Registro, con chiavi interne."""
    amount = float(tx.amount)
    is_transfer = tx.tx_type in _INTERNAL_TRANSFER_TYPES
    # Column order is the grid's order (founder, 2026-10-22): what you read,
    # what you decide, the rule tick, then the symbols that only inform.
    # Account and type are filters above the grid, not columns.
    row = {
        COL_ID:          tx.id,
        # U-06: resta un date, non una stringa, cosi' l'ordinamento e' corretto
        COL_DATE:        pd.to_datetime(tx.date).date() if tx.date else None,
        COL_DESC:        tx.description or "",
        # The bank's text right next to the counterpart, when asked: at the
        # far end of the grid it would mean scrolling to compare them.
        **({COL_RAW: tx.raw_description or ""} if show_raw else {}),
        COL_INCOME:      amount if amount > 0 else None,
        COL_EXPENSE:     abs(amount) if amount < 0 else None,
        COL_CAT_SUB:     join_cat_sub(tx.category, tx.subcategory),
        COL_CONTEXT:     tx.context or "",
        COL_VALIDATED:   bool(tx.human_validated),
        COL_TRANSFER:    is_transfer,
        COL_SEL:         False,
        COL_SOURCE:      source_badge.get(tx.category_source, "—"),
        COL_FLAG_REVIEW: "⚠️" if tx.to_review else "·",
        COL_FLAG_VALID:  "✅" if tx.human_validated else "·",
        COL_FLAG_TRANSFER: "🔄" if is_transfer else "·",
    }
    return row


logger = setup_logging()



def excluded_from_totals(txs) -> tuple[int, Decimal, Decimal]:
    """Movements shown in the table that the totals leave out (giroconti,
    card settlements): how many, how much went out, how much came in."""
    left_out = [Decimal(str(tx.amount)) for tx in txs if tx.tx_type in NOT_COUNTED_TYPES]
    return (len(left_out),
            abs(sum((a for a in left_out if a < 0), Decimal(0))),
            sum((a for a in left_out if a > 0), Decimal(0)))


_ALL_TX_TYPES = [
    "expense", "income", "card_tx",
    "internal_out", "internal_in", "card_settlement", "unknown",
]


def render_registry_page(engine):
    st.header(t("ledger.title"))

    cfg_svc  = SettingsService(engine)
    tx_svc   = TransactionService(engine)
    rule_svc = RuleService(engine)

    # ── Settings & taxonomy ────────────────────────────────────────────────────
    settings = cfg_svc.get_all()
    taxonomy = cfg_svc.get_taxonomy()

    _date_fmt = settings.get("date_display_format", "%d/%m/%Y")
    _date_fmt_js = strftime_to_momentjs(_date_fmt)
    _dec = settings.get("amount_decimal_sep", ",")
    _thou = settings.get("amount_thousands_sep", ".")
    giroconto_mode = settings.get("giroconto_mode", "neutral")

    _contexts = _contexts_of(settings)

    _expense_cats = sorted(taxonomy.all_expense_categories)
    _income_cats  = sorted(taxonomy.all_income_categories)
    _all_cats     = _expense_cats + _income_cats

    _accounts = tx_svc.get_distinct_account_labels()
    today = date.today()

    # ── Date preset initialisation (default: mese corrente) ───────────────────
    if "ledger_from" not in st.session_state:
        st.session_state["ledger_from"] = today.replace(day=1)
    if "ledger_to" not in st.session_state:
        st.session_state["ledger_to"] = today

    _first_cur  = today.replace(day=1)
    _three_ago  = today - timedelta(days=90)
    _first_year = today.replace(month=1, day=1)

    _cur_from = st.session_state.get("ledger_from", _first_cur)
    if not isinstance(_cur_from, date):
        _cur_from = _first_cur
    _rel_last_prev  = _cur_from - timedelta(days=1)
    _rel_first_prev = _rel_last_prev.replace(day=1)

    st.caption(t("ledger.quick_period"))
    pc1, pc2, pc3, pc4, pc5, pc6 = st.columns(6)
    if pc1.button(t("ledger.preset.current_month"), key="preset_cur",  use_container_width=True):
        st.session_state["ledger_from"] = _first_cur
        st.session_state["ledger_to"]   = today
    if pc2.button(t("ledger.preset.prev_month"), key="preset_prev", use_container_width=True):
        st.session_state["ledger_from"] = _rel_first_prev
        st.session_state["ledger_to"]   = _rel_last_prev
    if pc3.button(t("ledger.preset.last_3_months"), key="preset_3m",  use_container_width=True):
        st.session_state["ledger_from"] = _three_ago
        st.session_state["ledger_to"]   = today
    if pc4.button(t("ledger.preset.current_year"), key="preset_year", use_container_width=True):
        st.session_state["ledger_from"] = _first_year
        st.session_state["ledger_to"]   = today
    if pc5.button(t("ledger.preset.all"), key="preset_all",  use_container_width=True):
        for _k in ("ledger_from", "ledger_to"):
            if _k in st.session_state:
                del st.session_state[_k]
        st.rerun()
    if pc6.button(t("ledger.preset.reset"), key="preset_reset", use_container_width=True, type="secondary"):
        for _k in ("ledger_from", "ledger_to", "ledger_account", "ledger_type",
                   "ledger_cat", "ledger_ctx", "ledger_desc", "ledger_review", "ledger_hide_giro"):
            if _k in st.session_state:
                del st.session_state[_k]
        st.rerun()

    # ── Filter row ─────────────────────────────────────────────────────────────
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        date_from = st.date_input(t("ledger.filter.from"), key="ledger_from")
    with fc2:
        date_to = st.date_input(t("ledger.filter.to"), key="ledger_to")
    with fc3:
        account_filter = st.selectbox(
            t("ledger.filter.account"), [t("ledger.filter.account_all")] + _accounts, key="ledger_account"
        )
    with fc4:
        tx_type_filter = st.selectbox(t("ledger.filter.type"), [t("ledger.filter.type_all")] + _ALL_TX_TYPES, key="ledger_type")

    fc5, fc6, fc6b, fc7, fc8, fc9 = st.columns([3, 2, 1.5, 1, 1, 1])
    with fc5:
        desc_filter = st.text_input(
            t("ledger.filter.description"), placeholder=t("ledger.filter.description_placeholder"), key="ledger_desc"
        )
    with fc6:
        cat_filter = st.selectbox(
            t("ledger.filter.category"), [t("ledger.filter.category_all")] + _all_cats, key="ledger_cat"
        )
    with fc6b:
        ctx_filter = st.selectbox(
            t("ledger.filter.context"), [t("ledger.filter.context_all")] + _contexts, key="ledger_ctx"
        )
    with fc7:
        review_only = st.checkbox(t("ledger.filter.review_only"), key="ledger_review")
        # Movements of a place where everything is bought, not seen yet (spec 24).
        one_by_one_only = st.checkbox(t("ledger.filter.one_by_one"), key="ledger_one_by_one",
                                      help=t("ledger.filter.one_by_one_help"))
    with fc8:
        hide_giro = st.checkbox(
            t("ledger.filter.hide_giro"),
            key="ledger_hide_giro",
            value=st.session_state.get("ledger_hide_giro", giroconto_mode == "exclude"),
        )
    with fc9:
        show_raw = st.checkbox(t("ledger.filter.show_raw"), key="ledger_show_raw")

    # ── Build query filters ────────────────────────────────────────────────────
    filters: dict = {}
    if date_from:
        filters["date_from"] = date_from.isoformat()
    if date_to:
        filters["date_to"] = date_to.isoformat()
    if account_filter != t("ledger.filter.account_all"):
        filters["account_label"] = account_filter
    if tx_type_filter != t("ledger.filter.type_all"):
        filters["tx_type"] = tx_type_filter
    elif hide_giro:
        filters["exclude_tx_types"] = ["internal_in", "internal_out"]
    if desc_filter.strip():
        filters["description"] = desc_filter.strip()
    if cat_filter != t("ledger.filter.category_all"):
        filters["category"] = cat_filter
    if ctx_filter != t("ledger.filter.context_all"):
        filters["context"] = ctx_filter
    if review_only:
        filters["to_review"] = True
    if one_by_one_only:
        filters["category_source"] = "multi"

    txs = tx_svc.get_transactions(filters=filters)

    if not txs:
        st.info(t("ledger.no_transactions"))
        return

    # ── Metrics ────────────────────────────────────────────────────────────────
    _bal_txs = [tx for tx in txs if tx.tx_type not in NOT_COUNTED_TYPES]
    net       = sum(Decimal(str(tx.amount)) for tx in _bal_txs)
    income_t  = sum(Decimal(str(tx.amount)) for tx in _bal_txs if Decimal(str(tx.amount)) > 0)
    expense_t = sum(Decimal(str(tx.amount)) for tx in _bal_txs if Decimal(str(tx.amount)) < 0)

    m1, m2, m3, m4 = st.columns(4)
    _totals_help = t("ledger.metric.totals_help")
    m1.metric(t("ledger.metric.transactions"), len(txs))
    m2.metric(t("ledger.metric.net_balance"),  format_amount_display(float(net),            _dec, _thou),
              help=_totals_help)
    m3.metric(t("ledger.metric.income"),       format_amount_display(float(income_t),       _dec, _thou),
              help=_totals_help)
    m4.metric(t("ledger.metric.expenses"),     format_amount_display(float(abs(expense_t)), _dec, _thou),
              help=_totals_help)
    _n_out, _out_t, _in_t = excluded_from_totals(txs)
    if _n_out:
        st.caption(t("ledger.metric.excluded", n=_n_out,
                     out=format_amount_display(float(_out_t), _dec, _thou),
                     inc=format_amount_display(float(_in_t), _dec, _thou)))

    # ── Sort (U-05: sort on full dataset before pagination) ────────────────
    _sort_col, _sort_pg, _pg2 = st.columns([2, 2, 3])
    with _sort_col:
        _sort_options = {
            t("ledger.sort.date_desc"): ("date", True),
            t("ledger.sort.date_asc"): ("date", False),
            t("ledger.sort.amount_desc"): ("amount", True),
            t("ledger.sort.amount_asc"): ("amount", False),
            t("ledger.sort.desc_asc"): ("description", False),
            t("ledger.sort.cat_asc"): ("category", False),
        }
        _sort_label = st.selectbox(
            t("ledger.sort.label"), list(_sort_options.keys()), index=0, key="ledger_sort"
        )
        _sort_field, _sort_desc = _sort_options[_sort_label]
        txs = sorted(
            txs,
            key=lambda tx: getattr(tx, _sort_field, "") or "",
            reverse=_sort_desc,
        )

    # ── Pagination ────────────────────────────────────────────────────────────
    with _sort_pg:
        rows_per_page = st.selectbox(
            t("ledger.rows_per_page"), [15, 25, 50, 100, 200], index=0, key="ledger_page_size"
        )

    total_rows  = len(txs)
    total_pages = max(1, -(-total_rows // rows_per_page))

    _fp = f"{total_rows}_{sorted(filters.items())}_{_sort_label}"
    if st.session_state.get("_ledger_fp") != _fp:
        st.session_state["_ledger_fp"] = _fp
        st.session_state["ledger_page"] = 0

    page_num  = max(0, min(st.session_state.get("ledger_page", 0), total_pages - 1))
    page_start = page_num * rows_per_page
    page_end   = min(page_start + rows_per_page, total_rows)
    page_txs   = txs[page_start:page_end]

    with _pg2:
        st.caption(
            t("ledger.pagination", page=page_num + 1, total=total_pages,
              start=page_start + 1, end=page_end, rows=total_rows)
        )

    _render_grid(
        tx_svc, rule_svc, taxonomy, page_txs, show_raw=show_raw, _contexts=_contexts,
        _date_fmt_js=_date_fmt_js, _dec=_dec, _thou=_thou, filters=filters,
        page_num=page_num, total_pages=total_pages,
    )


def _contexts_of(settings: dict) -> list[str]:
    try:
        return json.loads(settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]'))
    except Exception:
        return ["Quotidianità", "Lavoro", "Vacanza"]


CHECK_PAGE_SIZE = 50


def render_check_grid(engine, txs: list, show_raw: bool = False) -> None:
    """The Registry grid on the movements of the checking flow (spec 31):
    the caller passes them already filtered and in date order; at most
    CHECK_PAGE_SIZE at a time, the next ones arrive as these are checked.
    *show_raw* adds the bank's text next to the description."""
    cfg_svc = SettingsService(engine)
    settings = cfg_svc.get_all()
    _render_grid(
        TransactionService(engine), RuleService(engine), cfg_svc.get_taxonomy(),
        txs[:CHECK_PAGE_SIZE], show_raw=show_raw, _contexts=_contexts_of(settings),
        _date_fmt_js=strftime_to_momentjs(settings.get("date_display_format", "%d/%m/%Y")),
        _dec=settings.get("amount_decimal_sep", ","), _thou=settings.get("amount_thousands_sep", "."),
        check={"size": len(txs)},
    )


# Session key: the version of the checking grid's editor, bumped at every save.
CHECK_GRID_VERSION = "_check_grid_version"
_CHECK_ALL_CONFIRM = "_check_all_confirm"


def _next_check_grid() -> None:
    st.session_state[CHECK_GRID_VERSION] = st.session_state.get(CHECK_GRID_VERSION, 0) + 1


def _render_check_all(tx_svc, ids: list[str], *, disabled: bool) -> None:
    """"All the movements shown are right", with a confirmation (spec 31)."""
    if not st.session_state.get(_CHECK_ALL_CONFIRM):
        if st.button(t("check.all_right", n=len(ids)), key="check_all", disabled=disabled or not ids,
                     use_container_width=True):
            st.session_state[_CHECK_ALL_CONFIRM] = True
            st.rerun()
        return
    st.warning(t("check.all_right_confirm", n=len(ids)))
    c_yes, c_no = st.columns(2)
    if c_yes.button(t("check.all_right_yes"), key="check_all_yes", type="primary"):
        n_ok = sum(1 for tx_id in ids if tx_svc.validate(tx_id))
        st.session_state.pop(_CHECK_ALL_CONFIRM, None)
        _next_check_grid()
        st.toast(t("ledger.validated_n", n=n_ok))
        st.rerun()
    if c_no.button(t("check.all_right_no"), key="check_all_no"):
        st.session_state.pop(_CHECK_ALL_CONFIRM, None)
        st.rerun()


def _render_grid(tx_svc, rule_svc, taxonomy, page_txs, *, show_raw: bool, _contexts: list[str],
                 _date_fmt_js: str, _dec: str, _thou: str, filters: dict | None = None,
                 page_num: int = 0, total_pages: int = 1, check: dict | None = None) -> None:
    """The Registry grid with everything it does on save: the rename
    question, the spread to similar movements, the rule from a row, page
    navigation and export. *check* is the checking flow (spec 31): no
    export or page navigation, checked rows leave the grid only at a save,
    and no save while a rule is in progress."""
    # ── Editable table ────────────────────────────────────────────────────────
    st.caption(
        t("ledger.edit_hint",
          cat=t("ledger.col.cat_sub"),
          ctx=t("ledger.col.context"), transfer=t("ledger.col.transfer").replace("🔄 ", ""),
          save=t("ledger.save_changes").replace("💾 ", ""))
    )

    _SOURCE_BADGE = {
        "llm": "🧠 AI",
        "rule": "📏 Regola",
        "manual": "👤 Manuale",
        "history": "📚 Storico",
        "multi": "🛒 " + t("rules.multi_value"),
    }

    orig_rows = [build_ledger_row(tx, show_raw, _SOURCE_BADGE) for tx in page_txs]
    orig_df = pd.DataFrame(orig_rows)
    # One menu for the whole column: only the categories of the direction
    # of the rows shown; with both directions on the page each row is
    # checked at the save.
    _amount_by_id = {tx.id: float(tx.amount or 0) for tx in page_txs}
    _cat_options = options_for_amounts(taxonomy, _amount_by_id.values())

    _col_cfg: dict = {
        COL_ID:          None,
        # Every column says what it means, on the header (founder, 2026-10-21:
        # a tick on the first column that writes a rule cannot be guessed).
        COL_SEL:         st.column_config.CheckboxColumn(
            t("ledger.col.rule_select"), help=t("ledger.col.rule_select_help"), width=80,
        ),
        COL_DATE:        st.column_config.DateColumn(
            t("ledger.col.date"), format=_date_fmt_js, width="small", help=t("ledger.col.date_help"),
        ),
        COL_DESC:        st.column_config.TextColumn(
            t("ledger.col.description"), width="large", help=t("ledger.col.description_help"),
        ),
        COL_INCOME:      st.column_config.NumberColumn(t("ledger.col.income"), disabled=True, format="%.2f",
                                                       width="small", help=t("ledger.col.income_help")),
        COL_EXPENSE:     st.column_config.NumberColumn(t("ledger.col.expense"), disabled=True, format="%.2f",
                                                       width="small", help=t("ledger.col.expense_help")),
        COL_CAT_SUB:     st.column_config.SelectboxColumn(
            t("ledger.col.cat_sub"), options=[""] + _cat_options, required=False, width="large",
            help=t("ledger.col.cat_sub_help"),
        ),
        COL_CONTEXT:     st.column_config.SelectboxColumn(
            t("ledger.col.context"), options=[""] + _contexts, required=False, width="small",
            help=t("ledger.col.context_help"),
        ),
        COL_SOURCE:      st.column_config.TextColumn(t("ledger.col.source"), disabled=True, width=100,
                                                     help=t("ledger.col.source_help")),
        COL_FLAG_REVIEW: st.column_config.TextColumn("⚠️", disabled=True, width=20,
                                                     help=t("ledger.col.flag_review_help")),
        COL_FLAG_VALID:  st.column_config.TextColumn("✅", disabled=True, width=20,
                                                     help=t("ledger.col.flag_valid_help")),
        COL_FLAG_TRANSFER: st.column_config.TextColumn("🔄", disabled=True, width=20,
                                                       help=t("ledger.col.flag_transfer_help")),
        COL_VALIDATED:   st.column_config.CheckboxColumn(
            t("ledger.col.validated"), help=t("ledger.col.validated_help"), width=60,
        ),
        COL_TRANSFER:    st.column_config.CheckboxColumn(t("ledger.col.transfer"), width="small",
                                                         help=t("ledger.col.transfer_help")),
    }
    if check is not None:
        _col_cfg[COL_FLAG_VALID] = None
    if show_raw:
        _col_cfg[COL_RAW] = st.column_config.TextColumn(
            t("ledger.col.raw"), disabled=True, width="medium", help=t("ledger.col.raw_help"),
        )

    edited_df = st.data_editor(
        orig_df,
        use_container_width=True,
        hide_index=True,
        height=min(650, 42 + len(orig_df) * 35),
        column_config=_col_cfg,
        # The checking grid starts a fresh editor after every save: its rows
        # change, and an unsaved edit must never slide onto another movement.
        key=f"check_editor_{st.session_state.get(CHECK_GRID_VERSION, 0)}" if check else "ledger_editor",
    )

    # ── Enforce single selection for rule creation ──────────────────────────
    _sel_indices = [i for i in range(len(edited_df)) if edited_df.iloc[i].get("_sel", False)]
    if len(_sel_indices) > 1:
        st.error(t("ledger.one_rule_at_a_time"))

    # ── Auto-save Validato checkbox changes (realtime) ───────────────────────
    # Not in the checking grid: a checked row leaves it, so the tick waits
    # for the save, together with the other edits (spec 31).
    _n_auto_val = 0
    for i in (range(len(edited_df)) if check is None else ()):
        new_val = bool(edited_df.iloc[i].get(COL_VALIDATED, False))
        old_val = bool(orig_df.iloc[i].get(COL_VALIDATED, False))
        if new_val != old_val:
            _tid = orig_df.iloc[i]["_id"]
            if new_val:
                tx_svc.validate(_tid)
            else:
                tx_svc.unvalidate(_tid)
            _n_auto_val += 1
    if _n_auto_val:
        st.toast(t("ledger.validations_updated", n=_n_auto_val))
        logger.info(f"ledger_page: auto-saved {_n_auto_val} validation changes")
        st.rerun()

    # ── Save & Validate buttons ──────────────────────────────────────────────
    _sel_ids = [
        orig_df.iloc[i]["_id"]
        for i in range(len(edited_df))
        if edited_df.iloc[i].get("_sel", False)
    ]
    # In the checking grid nothing refreshes while a rule is in progress:
    # the rule's own button saves the grid and writes the rule in one pass.
    _rule_pending = check is not None and len(_sel_ids) > 0
    sv_col, val_col, _ = st.columns([1, 1, 4])
    with sv_col:
        save_clicked = st.button(t("ledger.save_changes"), type="primary", key="ledger_save",
                                 use_container_width=True, disabled=_rule_pending)
    if _rule_pending:
        st.caption(t("check.rule_pending"))
    with val_col:
        if check is not None:
            _render_check_all(tx_svc, [tx.id for tx in page_txs], disabled=_rule_pending)
        elif st.button(t("ledger.validate_selected"), disabled=len(_sel_ids) == 0,
                       key="ledger_validate_bulk", use_container_width=True):
            n_ok = 0
            for _tid in _sel_ids:
                if tx_svc.validate(_tid):
                    n_ok += 1
            st.success(t("ledger.validated_n", n=n_ok))
            logger.info(f"ledger_page: validated {n_ok} transactions")
            st.rerun()

    def _save_edits() -> tuple[int, int, int, int, int, list[tuple[str, str]]]:
        """Write every edit of the grid; the counts and the movements whose
        new category may spread to similar ones."""
        logger.info("ledger_page: saving, comparing %d rows", len(orig_df))
        n_cat  = 0
        n_ctx  = 0
        n_giro = 0
        n_val  = 0
        n_desc = 0
        _fan_out_candidates: list[tuple[str, str]] = []  # (tx_id, description) for fan-out check
        for idx in range(len(orig_df)):
            orig = orig_df.iloc[idx]
            edit = edited_df.iloc[idx]
            tx_id = orig["_id"]

            cat_changed  = str(edit[COL_CAT_SUB] or "") != str(orig[COL_CAT_SUB] or "")
            ctx_changed  = str(edit[COL_CONTEXT])     != str(orig[COL_CONTEXT])
            giro_changed = bool(edit[COL_TRANSFER])   != bool(orig[COL_TRANSFER])
            val_changed  = bool(edit[COL_VALIDATED])  != bool(orig[COL_VALIDATED])
            desc_changed = str(edit[COL_DESC] or "").strip() != str(orig[COL_DESC] or "").strip()

            # The counterpart first: a category chosen in the same save looks
            # for similar movements by the new name.
            _desc_now = str(orig[COL_DESC]).strip()
            if desc_changed:
                if str(edit[COL_DESC] or "").strip():
                    if tx_svc.update_description(tx_id, edit[COL_DESC]):
                        n_desc += 1
                        _desc_now = str(edit[COL_DESC]).strip()
                        # Decided once for all the movements like this one
                        # (spec 27): asked right after the save.
                        st.session_state.setdefault("_own_name_pending", []).append(
                            (tx_id, _desc_now, len(rule_svc.same_movements(tx_id)),
                             rule_svc.other_amounts(tx_id)))
                else:
                    st.warning(t("ledger.description_empty", row=idx + 1))

            if cat_changed and edit[COL_CAT_SUB]:
                # One choice among the valid pairs: the subcategory always
                # belongs to its category.
                _new_cat, _new_sub = split_cat_sub(edit[COL_CAT_SUB])
                if wrong_direction(taxonomy, _new_cat, _amount_by_id.get(tx_id)):
                    st.error(t("ledger.wrong_direction", row=idx + 1, cat=_new_cat))
                    continue
                tx_svc.update_category(tx_id, _new_cat, _new_sub, origin="ledger")
                n_cat += 1
                if _desc_now:
                    _fan_out_candidates.append((tx_id, _desc_now))

            if ctx_changed:
                tx_svc.update_context(tx_id, edit[COL_CONTEXT] or None)
                n_ctx += 1

            if giro_changed:
                tx_svc.toggle_giroconto(tx_id)
                n_giro += 1

            if val_changed:
                logger.info("ledger_page: tx %s val_changed: %s -> %s", tx_id, orig[COL_VALIDATED], edit[COL_VALIDATED])
                if bool(edit[COL_VALIDATED]):
                    tx_svc.validate(tx_id)
                else:
                    tx_svc.unvalidate(tx_id)
                n_val += 1

        return n_cat, n_ctx, n_giro, n_val, n_desc, _fan_out_candidates

    if save_clicked:
        n_cat, n_ctx, n_giro, n_val, n_desc, _fan_out_candidates = _save_edits()
        total_saved = n_cat + n_ctx + n_giro + n_val + n_desc
        if total_saved and check is not None:
            _next_check_grid()
        if total_saved:
            parts = []
            if n_desc: parts.append(t("ledger.saved_descriptions", n=n_desc))
            if n_cat:  parts.append(f"{n_cat} categorie")
            if n_ctx:  parts.append(f"{n_ctx} contesti")
            if n_giro: parts.append(f"{n_giro} giroconti")
            if n_val:  parts.append(f"{n_val} validate")
            st.success(t("ledger.saved_summary", parts=" · ".join(parts)))
            logger.info(f"ledger_page: saved cat={n_cat} ctx={n_ctx} giro={n_giro}")

            # ── C-06: Fan-out — check for similar uncategorized transactions ──
            if _fan_out_candidates:
                _fan_out_all: dict[str, list] = {}  # tx_id -> similar txs
                for _fo_tx_id, _fo_desc in _fan_out_candidates:
                    _similar = tx_svc.find_similar_uncategorized(_fo_desc, _fo_tx_id)
                    if _similar:
                        _fan_out_all[_fo_tx_id] = _similar
                if _fan_out_all:
                    _total_similar = sum(len(v) for v in _fan_out_all.values())
                    st.session_state["_fan_out_pending"] = _fan_out_all
                    st.info(
                        t("ledger.fan_out.found", n=_total_similar)
                    )
                else:
                    st.rerun()
            else:
                st.rerun()
        else:
            st.info(t("ledger.no_changes"))

    # ── Own name for all the movements like this one (spec 27) ──────────────
    for _p_idx, (_on_id, _on_name, _on_n, _on_var) in enumerate(list(st.session_state.get("_own_name_pending", []))):
        _fmt = lambda v: format_amount_display(v, _dec, _thou, symbol="")  # noqa: E731
        if _on_var["n"]:
            # Same bank text, other amounts: an installment that changes
            # (spec 27). The sign-only rule is offered, never imposed.
            st.info(t("ledger.own_name.ask_variable", name=_on_name, n=_on_var["n"],
                      low=_fmt(_on_var["low"]), high=_fmt(_on_var["high"])))
        elif _on_n:
            st.info(t("ledger.own_name.ask", name=_on_name, n=_on_n))
        else:
            st.info(t("ledger.own_name.ask_future", name=_on_name))
        # A rule on the category too, offered as a choice (founder,
        # 2026-10-21: renaming many movements is the moment to fix it).
        _on_tx = rule_svc.movement_summary(_on_id)
        _with_rule = None
        if _on_tx["category"] and not _on_tx["giroconto"]:
            _with_rule = st.checkbox(
                t("ledger.own_name.category_rule",
                  cat=join_cat_sub(_on_tx["category"], _on_tx["subcategory"]), name=_on_name),
                value=True, key=f"own_name_rule_{_on_id}",
            )
        _c_all, _c_same, _c_one, _ = st.columns([2, 2, 1, 1])
        with _c_all:
            if _on_var["n"]:
                _label = t("ledger.own_name.all", n=_on_var["n"] + _on_n)
            else:
                _label = t("ledger.own_name.all", n=_on_n) if _on_n else t("ledger.own_name.future")
            if st.button(_label, key=f"own_name_all_{_on_id}", type="primary", use_container_width=True):
                _n_named = rule_svc.create_own_name(_on_id, _on_name, any_amount=bool(_on_var["n"]),
                                                    category_rule=_with_rule)
                st.session_state["_own_name_pending"].pop(_p_idx)
                st.toast(t("ledger.own_name.done", name=_on_name, n=_n_named))
                st.rerun()
        if _on_var["n"]:
            with _c_same:
                if st.button(t("ledger.own_name.same_amount", n=_on_n), key=f"own_name_same_{_on_id}",
                             use_container_width=True):
                    _n_named = rule_svc.create_own_name(_on_id, _on_name, category_rule=_with_rule)
                    st.session_state["_own_name_pending"].pop(_p_idx)
                    st.toast(t("ledger.own_name.done", name=_on_name, n=_n_named))
                    st.rerun()
        with _c_one:
            if st.button(t("ledger.own_name.only_this"), key=f"own_name_one_{_on_id}", use_container_width=True):
                st.session_state["_own_name_pending"].pop(_p_idx)
                st.rerun()
        break  # one question at a time

    # ── C-06: Fan-out action buttons ─────────────────────────────────────────
    if st.session_state.get("_fan_out_pending"):
        _fan_out_data = st.session_state["_fan_out_pending"]
        _total_fan = sum(len(v) for v in _fan_out_data.values())
        fo_col1, fo_col2, _ = st.columns([1, 1, 4])
        with fo_col1:
            if st.button(
                t("ledger.fan_out.apply_all", n=_total_fan),
                key="ledger_fan_out_apply",
                type="primary",
                use_container_width=True,
            ):
                _n_applied = 0
                for _src_id, _targets in _fan_out_data.items():
                    _n_applied += tx_svc.apply_fan_out(
                        _src_id, [t.id for t in _targets]
                    )
                del st.session_state["_fan_out_pending"]
                st.toast(t("ledger.fan_out.applied", n=_n_applied))
                logger.info(f"ledger_page: fan-out applied to {_n_applied} transactions")
                st.rerun()
        with fo_col2:
            if st.button(
                t("ledger.fan_out.skip"),
                key="ledger_fan_out_skip",
                use_container_width=True,
            ):
                del st.session_state["_fan_out_pending"]
                st.rerun()

    # ── Crea regola dalla selezione ──────────────────────────────────────────
    if len(_sel_ids) == 1:
        _rule_tx_id = _sel_ids[0]
        _rule_tx_row = orig_df[orig_df["_id"] == _rule_tx_id].iloc[0]
        _rule_tx_desc = _rule_tx_row[COL_DESC]
        _rule_tx_cat_sub = _rule_tx_row[COL_CAT_SUB]
        _rule_tx_ctx = _rule_tx_row[COL_CONTEXT]

        # The product proposes the rule (spec 18): merchant name and "starts
        # with" when it can tell the name, else the exact description. Asked
        # once per movement, not at every rerun of the page.
        _suggest_key = f"_rule_suggest_{_rule_tx_id}"
        if _suggest_key not in st.session_state:
            st.session_state[_suggest_key] = rule_svc.write_rules([_rule_tx_desc])[_rule_tx_desc]
        _sugg_pattern, _sugg_type = st.session_state[_suggest_key]

        with st.expander(t("ledger.rule_from_selection"), expanded=True):
            rc1, rc2 = st.columns([3, 1])
            with rc1:
                rule_pattern = st.text_input(
                    t("ledger.rule.pattern"), value=_sugg_pattern,
                    key=f"rule_create_pattern_{_rule_tx_id}",
                )
            with rc2:
                # etichetta tradotta -> valore interno, che resta stabile
                _match_labels = {
                    t("ledger.rule.match.starts_with"): STARTS_WITH,
                    t("ledger.rule.match.contains"): "contains",
                    t("ledger.rule.match.exact"):    "exact",
                    t("ledger.rule.match.regex"):    "regex",
                }
                _match_values = list(_match_labels.values())
                _match_label = st.selectbox(
                    t("ledger.rule.match_type"), list(_match_labels.keys()),
                    index=_match_values.index(_sugg_type) if _sugg_type in _match_values else 0,
                    key=f"rule_create_match_type_{_rule_tx_id}",
                )
                rule_match_type = _match_labels[_match_label]

            rc3, rc5, rc6 = st.columns([2, 1, 1])
            with rc3:
                _rc_cat, _rc_sub = split_cat_sub(_rule_tx_cat_sub)
                rule_category, rule_subcategory = cat_sub_select(
                    t("ledger.col.cat_sub"), taxonomy, _rc_cat, _rc_sub,
                    key=f"rule_create_category_{_rule_tx_id}",
                    only=direction_of(_amount_by_id.get(_rule_tx_id)),
                )
            with rc5:
                _ctx_options = [t("ledger.rule.no_context")] + _contexts
                _rc_ctx_idx = (_ctx_options.index(_rule_tx_ctx)
                               if _rule_tx_ctx in _ctx_options else 0)
                rule_context = st.selectbox(
                    t("ledger.col.context"), options=_ctx_options,
                    index=_rc_ctx_idx, key="rule_create_context",
                )
            with rc6:
                rule_priority = st.number_input(
                    t("ledger.rule.priority"), value=10, min_value=0, max_value=100,
                    key="rule_create_priority",
                )

            # Check if rule already exists + preview
            _rule_exists = False
            if rule_pattern.strip():
                _existing_rules = rule_svc.get_rules()
                _rule_exists = any(
                    r.pattern.lower() == rule_pattern.strip().lower()
                    and r.match_type == rule_match_type
                    for r in _existing_rules
                )
                _rule_matching = tx_svc.get_by_rule_pattern(
                    rule_pattern.strip(), rule_match_type,
                    "out" if _amount_by_id.get(_rule_tx_id, 0) < 0 else "in",
                )
                if _rule_exists:
                    st.warning(t("ledger.rule_exists_update", n=len(_rule_matching)))
                else:
                    st.info(t("ledger.rule_will_match", n=len(_rule_matching)))

            _btn_label = t("ledger.rule_apply_update" if _rule_exists else "ledger.rule_apply_create")
            if st.button(_btn_label, key="rule_create_apply"):
                if check is not None:
                    # One pass: the grid's edits, then the rule, then a
                    # single refresh (spec 31, rules from the grid).
                    _save_edits()
                    _next_check_grid()
                _ctx_val = rule_context if rule_context != t("ledger.rule.no_context") else None
                _, _created = rule_svc.create_rule(
                    pattern=rule_pattern.strip(),
                    match_type=rule_match_type,
                    category=rule_category,
                    subcategory=rule_subcategory,
                    context=_ctx_val,
                    priority=rule_priority,
                )
                n_matched, n_cleared = rule_svc.apply_to_all()
                # chiave diversa per creata/aggiornata: in altre lingue la frase
                # non si compone incollando un participio
                st.toast(t("ledger.rule_created" if _created else "ledger.rule_updated",
                           n=n_matched))
                logger.info(
                    f"ledger_page: rule created pattern={rule_pattern!r} "
                    f"matched={n_matched} cleared={n_cleared}"
                )
                st.rerun()
    else:
        st.caption(t("ledger.select_row_for_rule"))

    if check is not None:
        return

    # ── Page navigation ───────────────────────────────────────────────────────
    nav1, nav2, _ = st.columns([1, 1, 5])
    with nav1:
        if st.button(t("ledger.page_prev"), disabled=(page_num == 0), key="ledger_prev",
                     use_container_width=True):
            st.session_state["ledger_page"] = page_num - 1
            st.rerun()
    with nav2:
        if st.button(t("ledger.page_next"), disabled=(page_num >= total_pages - 1), key="ledger_next",
                     use_container_width=True):
            st.session_state["ledger_page"] = page_num + 1
            st.rerun()

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    ec1, ec2 = st.columns(2)
    with ec1:
        csv_bytes = tx_svc.export_csv(filters=filters)
        offer_file(
            t("ledger.export_csv"), csv_bytes, "spendifai_export.csv", "text/csv",
            key="ledger_csv", use_container_width=True,
        )
    with ec2:
        xlsx_bytes = tx_svc.export_xlsx(filters=filters)
        offer_file(
            t("ledger.export_xlsx"), xlsx_bytes, "spendifai_export.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="ledger_xlsx", use_container_width=True,
        )
