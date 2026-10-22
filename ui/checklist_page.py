"""Check List — presenza transazioni per mese e conto.

Mostra una tabella pivot mese × conto con il conteggio delle transazioni.
I mesi sono in ordine decrescente (corrente in cima); i conti senza
transazioni per quel mese mostrano un'icona di assenza.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd
import streamlit as st

from services.guided_flow_service import (
    CHECK_WINDOW_MONTHS,
    GuidanceKind,
    GuidedFlowService,
    group_by_account,
)
from services.held_movement_service import HeldMovementService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.formatting import format_amount_display
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.file_handoff import offer_file
from ui.widgets.immersive import go_to
from ui.widgets.staged_import import (
    StagedFile, files_and_rows, stage_file, staged_files, staged_rows, unstage_file,
)

logger = setup_logging()


# ── Costanti di visualizzazione ───────────────────────────────────────────────

_ICON_NO_TX    = "—"
_ICON_HAS_TX   = ""
_COLOR_NO_TX   = "#c8c8c8"
_COLOR_LOW     = "#9ecae1"
_COLOR_MED     = "#4292c6"
_COLOR_HIGH    = "#084594"
_COLOR_TEXT_DK = "#ffffff"


def _month_label(ym: str) -> str:
    """'2025-03' → 'Mar 2025'"""
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return ym


def _cell_color(val: int) -> str:
    if val == 0:
        return f"color: {_COLOR_NO_TX}; font-style: italic"
    if val < 5:
        return f"background-color: {_COLOR_LOW}; color: #003366"
    if val < 20:
        return f"background-color: {_COLOR_MED}; color: {_COLOR_TEXT_DK}"
    return f"background-color: {_COLOR_HIGH}; color: {_COLOR_TEXT_DK}"


def _cell_fmt(val: int) -> str:
    return _ICON_NO_TX if val == 0 else str(val)


def _all_months_range(oldest_ym: str, newest_ym: str) -> list[str]:
    """Return every 'YYYY-MM' string from newest_ym down to oldest_ym (inclusive)."""
    y_new, m_new = int(newest_ym[:4]), int(newest_ym[5:])
    y_old, m_old = int(oldest_ym[:4]), int(oldest_ym[5:])
    months = []
    y, m = y_new, m_new
    while (y, m) >= (y_old, m_old):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


def _build_pivot(
    rows: list,
    all_accounts: list[str],
    current_ym: str,
) -> pd.DataFrame:
    """Build a month × account DataFrame of tx counts."""
    data_months = [r.year_month for r in rows if r.year_month]
    oldest_ym = min(data_months) if data_months else current_ym

    months_sorted = _all_months_range(oldest_ym, current_ym)

    data: dict[str, dict[str, int]] = {ym: {} for ym in months_sorted}
    for r in rows:
        ym = r.year_month
        if not ym:
            continue
        acc = r.account_label or "(nessun conto)"
        if ym in data:
            data[ym][acc] = int(r.tx_count)

    records = []
    for ym in months_sorted:
        row: dict = {"Mese": _month_label(ym)}
        for acc in all_accounts:
            row[acc] = data[ym].get(acc, 0)
        records.append(row)

    df = pd.DataFrame(records).set_index("Mese")
    return df


# ── Guidance: what to download next ──────────────────────────────────────────

def _fmt_date(d: date) -> str:
    return t("common.date_long", d=d.day, month=t(f"common.month.{d.month}"), y=d.year)


def _fmt_month(d: date) -> str:
    return t("common.month_year", month=t(f"common.month.{d.month}"), y=d.year)


def _guidance_sentence(g) -> str:
    """One sentence that says what to download, with the exact dates."""
    if g.kind is GuidanceKind.up_to_date:
        return t("guided.checklist.up_to_date")
    return t(
        f"guided.checklist.{g.kind.value}",
        account=g.account,
        date_from=_fmt_date(g.date_from),
        date_to=_fmt_date(g.date_to),
        month=_fmt_month(g.last_loaded) if g.last_loaded else "",
    )


def _render_guidance(flow_svc: GuidedFlowService) -> bool:
    """Render the sentence above the grid. False when there is nothing
    else to show (no account yet): the grid would be empty."""
    guidance = flow_svc.checklist_guidance()
    first = guidance[0]

    if first.kind is GuidanceKind.no_accounts:
        with st.container(border=True):
            st.markdown(t("guided.checklist.no_accounts"))
            with st.form("guided_add_account", clear_on_submit=True):
                col_name, col_btn = st.columns([4, 1], vertical_alignment="bottom")
                name = col_name.text_input(t("guided.checklist.add_account_label"))
                if col_btn.form_submit_button(t("guided.checklist.add_account_btn"), width="stretch"):
                    if name.strip():
                        try:
                            flow_svc.add_account(name)
                            st.session_state["_guided_account_added"] = name.strip()
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                    else:
                        st.warning(t("settings.account_name_empty"))
        return False

    added = st.session_state.pop("_guided_account_added", None)
    if added:
        st.success(t("guided.checklist.account_added", account=added))

    groups = group_by_account(guidance)
    with st.container(border=True):
        _render_group(*groups[0], flow_svc=flow_svc, primary=True)
        rest = groups[1:]
        if rest:
            st.caption(t("guided.checklist.also"))
            # A long list scrolls in its own box instead of pushing the grid down.
            lines = sum(len(items) for _, items in rest)
            box = (st.container(height=_SCROLL_HEIGHT_PX, border=False)
                   if lines > _SCROLL_AFTER_LINES else st.container())
            with box:
                for account, items in rest:
                    _render_group(account, items, flow_svc=flow_svc)
        for err in st.session_state.pop("_stage_errors", []):
            st.warning(err)
        staged = staged_files()
        if staged and st.button(
            t("guided.checklist.import_n", what=files_and_rows(
                len(staged), staged_rows(flow_svc.count_rows))),
            type="primary", key="import_staged",
        ):
            go_to("import")
        old = flow_svc.old_holes()
        if old:
            st.caption(t("guided.checklist.old_holes", n=CHECK_WINDOW_MONTHS,
                         items=", ".join(f"{a} ({n})" for a, n in old.items())))
    _render_closed(flow_svc)
    return True


_SCROLL_AFTER_LINES = 4
_SCROLL_HEIGHT_PX = 240


# Account | what is missing | upload | close: the same widths on every row,
# so the buttons line up down the list.
_GROUP_COLUMNS = [1, 4, 1, 1]


def _render_group(account: str | None, items: list, flow_svc: GuidedFlowService,
                  primary: bool = False) -> None:
    """One row per suggestion; the account and its two actions on its first row.

    Both actions belong to the account: *Upload the file* opens the import
    with this account already chosen, *I no longer use it* closes it.
    """
    if account is None:
        for g in items:
            st.markdown(_guidance_sentence(g))
        return
    for i, g in enumerate(items):
        col_account, col_text, col_upload, col_close = st.columns(
            _GROUP_COLUMNS, gap="small", vertical_alignment="center")
        col_text.markdown(_without_account(_guidance_sentence(g), account))
        if i:
            continue
        col_account.markdown(f"**{account}**")
        _upload_control(col_upload, account, primary, flow_svc)
        _close_control(col_close, account, flow_svc)
    _render_staged_for(account)


def _upload_control(col, account: str, primary: bool, flow_svc: GuidedFlowService) -> None:
    """Pick this account's files and put them on hold for the import.

    The page does not change: several accounts can be prepared, then one
    import runs them all with their account already assigned.
    """
    with col.popover(t("guided.checklist.go_import"), width="stretch",
                     type="primary" if primary else "secondary"):
        # a new key empties the picker once its files are on hold
        round_key = f"_stage_round_{account}"
        picked = st.file_uploader(
            t("guided.checklist.stage_label", account=account),
            type=["csv", "xls", "xlsx"], accept_multiple_files=True,
            key=f"stage_up_{account}_{st.session_state.get(round_key, 0)}",
        )
        if picked and st.button(t("guided.checklist.stage_add"), key=f"stage_add_{account}",
                                type="primary"):
            for uf in picked:
                data = uf.getvalue()
                owner = flow_svc.format_owner(data, uf.name)
                if not stage_file(StagedFile(uf.name, data, account,
                                             owner if owner and owner != account else None)):
                    st.session_state.setdefault("_stage_errors", []).append(
                        t("guided.checklist.dup_name", file=uf.name))
            st.session_state[round_key] = st.session_state.get(round_key, 0) + 1
            st.rerun()


def _render_staged_for(account: str) -> None:
    """The files on hold for this account, each with a remove button."""
    mine = [f for f in staged_files() if f.account == account]
    if not mine:
        return
    _, col_text, _, _ = st.columns(_GROUP_COLUMNS, gap="small", vertical_alignment="center")
    with col_text:
        cols = st.columns([2] + [1] * len(mine), vertical_alignment="center")
        cols[0].caption(t("guided.checklist.staged"))
        for col, f in zip(cols[1:], mine):
            if col.button(f"✕ {f.name}", key=f"unstage_{f.name}", type="tertiary"):
                unstage_file(f.name)
                st.rerun()
        for f in mine:
            if f.format_owner:
                st.warning(t("guided.checklist.other_account", file=f.name, other=f.format_owner))


def _close_control(col, account: str, flow_svc: GuidedFlowService) -> None:
    """"I no longer use it": close the account from the date picked."""
    with col.popover(t("guided.checklist.close_btn"), width="stretch"):
        st.caption(t("guided.checklist.close_help"))
        closed_on = st.date_input(
            t("guided.checklist.close_date"),
            value=flow_svc.suggested_closing_date(account),
            key=f"close_date_{account}",
            format="DD/MM/YYYY",
        )
        if st.button(t("guided.checklist.close_confirm"), key=f"close_btn_{account}", type="primary"):
            flow_svc.close_account(account, closed_on)
            st.rerun()


def _render_closed(flow_svc: GuidedFlowService) -> None:
    """Closed accounts: when, how many holes they were closed with, Reopen."""
    closed = flow_svc.closed_accounts()
    if not closed:
        return
    with st.expander(t("guided.checklist.closed_title", n=len(closed))):
        for c in closed:
            col_account, col_text, col_btn = st.columns([1, 4, 1], gap="small",
                                                        vertical_alignment="center")
            col_account.markdown(f"**{c.account}**")
            notes = [t("guided.checklist.closed_on", date=_fmt_date(c.closed_on))]
            if c.holes == 1:
                notes.append(t("guided.checklist.closed_holes_one"))
            elif c.holes > 1:
                notes.append(t("guided.checklist.closed_holes_many", n=c.holes))
            if c.after_closing:
                notes.append(t("guided.checklist.closed_after"))
            col_text.markdown(" ".join(n[:1].upper() + n[1:] for n in notes))
            if col_btn.button(t("guided.checklist.reopen_btn"), key=f"reopen_{c.account}"):
                flow_svc.reopen_account(c.account)
                st.rerun()


def _render_held(held_svc: HeldMovementService) -> None:
    """Movements set aside at import because their date is in the future: fix
    the date and import them, or discard them. Nothing else counts them."""
    for err in st.session_state.pop("_held_errors", []):
        st.warning(err)
    rows = held_svc.held()
    if not rows:
        return
    st.warning(t("guided.checklist.held", n=len(rows)))
    c = {k: t(f"guided.checklist.held_col_{k}") for k in (
        "read", "account", "description", "amount", "fixed", "action")}
    do_import, do_discard = t("guided.checklist.held_import"), t("guided.checklist.held_discard")
    df = pd.DataFrame([{
        c["read"]: _fmt_date(r.date),
        c["account"]: r.account,
        c["description"]: r.description,
        c["amount"]: format_amount_display(float(r.amount)),
        c["fixed"]: r.suggested,
        c["action"]: "",
    } for r in rows])
    edited = st.data_editor(
        df,
        column_config={
            **{c[k]: st.column_config.TextColumn(c[k], disabled=True)
               for k in ("read", "account", "description", "amount")},
            c["fixed"]: st.column_config.DateColumn(
                c["fixed"], max_value=date.today(), format="DD/MM/YYYY"),
            c["action"]: st.column_config.SelectboxColumn(
                c["action"], options=["", do_import, do_discard]),
        },
        hide_index=True, num_rows="fixed", width="stretch", key="held_editor",
    )
    st.caption(t("guided.checklist.held_help"))
    if st.button(t("guided.checklist.held_apply"), key="held_apply", type="primary"):
        done = dup = 0
        for idx, r in enumerate(rows):
            action = edited.at[idx, c["action"]] or ""
            if action == do_discard:
                held_svc.discard(r.id)
                done += 1
            elif action == do_import:
                fixed = edited.at[idx, c["fixed"]]
                if fixed is None or pd.isna(fixed):
                    st.session_state.setdefault("_held_errors", []).append(
                        t("guided.checklist.held_no_date", description=r.description))
                    continue
                fixed = pd.Timestamp(fixed).date()
                if fixed > date.today():
                    st.session_state.setdefault("_held_errors", []).append(
                        t("guided.checklist.held_still_future", description=r.description))
                    continue
                if not held_svc.release(r.id, fixed):
                    dup += 1
                done += 1
        logger.info(f"checklist_page: held movements handled={done} already_present={dup}")
        if dup:
            st.session_state.setdefault("_held_errors", []).append(
                t("guided.checklist.held_duplicate", n=dup))
        st.rerun()


def _without_account(sentence: str, account: str | None) -> str:
    """Drop the leading "**account**:" every per-account sentence starts with,
    once the account is already the heading of its group."""
    if not account:
        return sentence
    rest = re.sub(rf"^\*\*{re.escape(account)}\*\*\s*:\s*", "", sentence)
    return rest[:1].upper() + rest[1:] if rest != sentence else sentence


def render_checklist_page(engine) -> None:
    st.header(t("checklist.title"))
    st.caption(t("checklist.caption"))

    held_svc = HeldMovementService(engine)
    held_svc.hold_existing()          # future dates imported before the check existed
    if not _render_guidance(GuidedFlowService(engine)):
        return
    _render_held(held_svc)

    cfg_svc = SettingsService(engine)
    tx_svc  = TransactionService(engine)

    today = date.today()
    current_ym = today.strftime("%Y-%m")

    # ── Carica dati ───────────────────────────────────────────────────────────
    account_rows      = cfg_svc.get_accounts()
    defined_accounts  = [a.name for a in account_rows]
    tx_account_labels = tx_svc.get_distinct_account_labels()
    tx_account_set    = set(tx_account_labels)
    extra             = sorted(tx_account_set - set(defined_accounts))
    all_accounts      = defined_accounts + extra

    count_rows = tx_svc.get_monthly_tx_counts()

    # ── Stato vuoto ───────────────────────────────────────────────────────────
    if not all_accounts and not count_rows:
        st.info(t("checklist.no_data"))
        return

    if not all_accounts:
        all_accounts = sorted({r.account_label for r in count_rows if r.account_label})

    # ── Pivot table ───────────────────────────────────────────────────────────
    df = _build_pivot(count_rows, all_accounts, current_ym)

    # ── Metriche sommario ─────────────────────────────────────────────────────
    total_tx = sum(r.tx_count for r in count_rows)
    n_months_with_data = len({r.year_month for r in count_rows if r.year_month})
    col1, col2, col3 = st.columns(3)
    col1.metric(t("checklist.total_tx"), f"{total_tx:,}".replace(",", "."))
    col2.metric(t("checklist.accounts_monitored"), len(all_accounts))
    col3.metric(t("checklist.months_with_data"), n_months_with_data)

    st.divider()

    # ── Filtro opzionale ──────────────────────────────────────────────────────
    with st.expander(t("checklist.filters"), expanded=False):
        filter_accounts = st.multiselect(
            t("checklist.filter_accounts"),
            options=all_accounts,
            default=[],
            help=t("checklist.filter_accounts_help"),
        )
        col_a, col_b = st.columns(2)
        max_months = col_a.number_input(
            t("checklist.last_n_months"),
            min_value=0, max_value=120, value=0, step=1,
        )
        hide_empty_rows = col_b.checkbox(
            t("checklist.hide_empty"),
            value=False,
            help=t("checklist.hide_empty_help"),
        )

    display_df = df.copy()
    if filter_accounts:
        cols_to_keep = [c for c in display_df.columns if c in filter_accounts]
        display_df = display_df[cols_to_keep]

    if hide_empty_rows:
        display_df = display_df[display_df.sum(axis=1) > 0]

    if max_months and max_months > 0:
        display_df = display_df.head(int(max_months))

    # ── Visualizzazione ───────────────────────────────────────────────────────
    if display_df.empty:
        st.warning(t("checklist.no_filtered_data"))
        return

    closed_names = {a.name for a in account_rows if getattr(a, "closed_on", None)}
    display_df = display_df.rename(columns={
        c: t("checklist.closed_col", account=c) for c in display_df.columns if c in closed_names
    })

    styled = (
        display_df.style
        .map(_cell_color)
        .format(_cell_fmt)
        .set_properties(**{"text-align": "center", "min-width": "80px"})
        .set_table_styles([
            {"selector": "th", "props": [("text-align", "center"), ("font-size", "13px")]},
            {"selector": "td", "props": [("font-size", "14px"), ("padding", "6px 12px")]},
            {"selector": "th.row_heading", "props": [("text-align", "left"), ("min-width", "90px")]},
        ])
    )

    st.dataframe(styled, use_container_width=True)

    with st.expander(t("checklist.color_legend"), expanded=False):
        st.markdown(
            f"""
| Colore | Significato |
|---|---|
| — (grigio chiaro) | Nessuna transazione |
| 🔵 Azzurro tenue | 1–4 transazioni |
| 🔵 Azzurro medio | 5–19 transazioni |
| 🔵 Azzurro scuro | ≥ 20 transazioni |
"""
        )

    csv = display_df.reset_index().to_csv(index=False).encode("utf-8")
    offer_file(
        "⬇️ Scarica CSV",
        csv,
        f"checklist_{today.strftime('%Y%m%d')}.csv",
        "text/csv",
    )
