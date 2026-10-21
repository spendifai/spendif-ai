"""Rules management page — view, edit, delete and create category rules."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from services.intermediary_service import IntermediaryService
from services.rule_service import KEY_CHECK, KEY_KNOWN, STARTS_WITH, RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from support.logging import setup_logging
from ui.i18n import t
from ui.widgets.cat_select import build_cat_options, cat_sub_select, join_cat_sub, split_cat_sub

logger = setup_logging()


def _match_labels() -> dict[str, str]:
    return {
        t("rules.match_contains"): "contains",
        t("rules.match_starts_with"): STARTS_WITH,
        t("rules.match_exact"): "exact",
        t("rules.match_regex"): "regex",
    }


def _label_from_type() -> dict[str, str]:
    return {v: k for k, v in _match_labels().items()}


_NO_CONTEXT   = "— nessuno —"


def render_rules_page(engine):
    st.header(t("rules.title"))
    st.caption(t("rules.caption"))

    rule_svc = RuleService(engine)
    cfg_svc  = SettingsService(engine)
    tx_svc   = TransactionService(engine)

    taxonomy       = cfg_svc.get_taxonomy()
    settings       = cfg_svc.get_all()
    rules          = rule_svc.get_rules()

    try:
        _contexts: list[str] = json.loads(
            settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]')
        )
    except Exception:
        _contexts = ["Quotidianità", "Lavoro", "Vacanza"]
    _ctx_options = [_NO_CONTEXT] + _contexts

    match_labels = _match_labels()
    label_from_type = _label_from_type()

    if not rules:
        st.info(t("rules.no_rules"))
    else:
        # ── Tabella regole ──────────────────────────────────────────────────
        st.subheader(t("rules.active_rules", n=len(rules)))
        # Rules on a payment intermediary take nothing once the merchant is
        # written instead (spec 20): said, never deleted.
        # Only the rules that really take nothing any more: a rule on a payment
        # service still covers the movements where the bank does not write
        # the shop (a direct debit to PayPal), and those are not said dead.
        _on_inter = [r for r in IntermediaryService(engine).rules_on_intermediaries(rules)
                     if not tx_svc.get_by_rule_pattern(r.pattern, r.match_type, r.direction)]
        if _on_inter:
            st.warning(t("rules.on_intermediary", n=len(_on_inter),
                         rules=", ".join(f"{r.pattern}" for r in _on_inter)))
            # Never deleted by the product: the user decides, with one click.
            if st.button(t("rules.on_intermediary_delete", n=len(_on_inter)),
                         key="rules_delete_on_intermediary"):
                for _r in _on_inter:
                    rule_svc.delete_rule(_r.id)
                st.rerun()
        # How sure it is that a "starts with" rule names the merchant: worked
        # out from the rule each time, never stored (spec 18).
        _key_label = {KEY_KNOWN: t("rules.key.known"), KEY_CHECK: t("rules.key.check")}
        _conf = {r.id: rule_svc.key_confidence(r.pattern, r.match_type) for r in rules}
        shown = rules
        _q = st.text_input(t("rules.search"), key="rules_search",
                           placeholder=t("rules.search_placeholder"))
        if _q.strip():
            shown = [r for r in shown
                     if tx_svc.text_matches(_q, r.pattern, r.category, r.subcategory)]
            if not shown:
                st.caption(t("rules.search_none", query=_q.strip()))
        if st.toggle(t("rules.filter_key_check"), value=False, key="rules_filter_key_check"):
            shown = [r for r in shown if _conf[r.id] == KEY_CHECK]
            if not shown:
                st.caption(t("rules.no_key_check"))
        table_data = [
            {
                "ID": r.id,
                "Pattern": r.pattern,
                t("rules.col_type"): label_from_type.get(r.match_type, r.match_type),
                t("rules.col_key"): _key_label.get(_conf[r.id], ""),
                t("rules.col_direction"): (t("rules.direction_out") if r.direction == "out"
                                           else t("rules.direction_in") if r.direction == "in" else ""),
                t("ledger.col.cat_sub"): (
                    t("rules.giroconto_value") if r.is_giroconto
                    else t("rules.multi_value") if r.is_multi_category
                    else join_cat_sub(r.category, r.subcategory)
                ),
                t("rules.col_context"): r.context or "",
                t("rules.col_priority"): r.priority,
            }
            for r in sorted(shown, key=lambda x: x.pattern.casefold())
        ]
        df_rules = pd.DataFrame(table_data, columns=[
            "ID", "Pattern", t("rules.col_type"), t("rules.col_key"), t("rules.col_direction"),
            t("ledger.col.cat_sub"),
            t("rules.col_context"), t("rules.col_priority"),
        ])

        # ── Ordinamento esplicito ────────────────────────────────────
        _sort_cols = ["Pattern", t("ledger.col.cat_sub"), t("rules.col_priority"),
                      t("rules.col_context")]
        _sc1, _sc2, _ = st.columns([2, 1, 4])
        with _sc1:
            _sort_col = st.selectbox(t("rules.sort_by"), _sort_cols, key="rules_sort_col")
        with _sc2:
            _sort_asc = st.toggle(t("rules.sort_asc"), value=True, key="rules_sort_asc")
        df_rules = df_rules.sort_values(
            _sort_col, ascending=_sort_asc,
            key=lambda s: s.str.casefold() if s.dtype == object else s,
        ).reset_index(drop=True)

        # ── Paginazione ──────────────────────────────────────────────
        _PAGE_SIZE = 20
        _n_pages   = max(1, (len(df_rules) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        _pg_col, _ = st.columns([2, 5])
        with _pg_col:
            _page = st.number_input(
                t("rules.page_label", max=_n_pages), min_value=1, max_value=_n_pages,
                value=1, step=1, key="rules_page_num",
            )
        _start = (_page - 1) * _PAGE_SIZE
        _end   = _start + _PAGE_SIZE
        # Edited in the grid, like every grid of the app: one menu for
        # category and subcategory (giroconto and "everything is bought"
        # are two more choices), context and priority.
        _col_cs = t("ledger.col.cat_sub")
        _giro_v, _multi_v = t("rules.giroconto_value"), t("rules.multi_value")
        _page_df = df_rules.iloc[_start:_end].reset_index(drop=True)
        _edited_rules = st.data_editor(
            _page_df,
            use_container_width=True,
            hide_index=True,
            disabled=["ID", "Pattern", t("rules.col_type"), t("rules.col_key"), t("rules.col_direction")],
            column_config={
                _col_cs: st.column_config.SelectboxColumn(
                    _col_cs, options=build_cat_options(taxonomy) + [_giro_v, _multi_v],
                    required=True, width="large",
                ),
                t("rules.col_context"): st.column_config.SelectboxColumn(
                    t("rules.col_context"), options=[""] + _contexts, required=False,
                ),
                t("rules.col_priority"): st.column_config.NumberColumn(
                    t("rules.col_priority"), min_value=0, max_value=100, step=1,
                ),
            },
            key=f"rules_grid_{_page}_{_sort_col}_{_sort_asc}",
        )
        if st.button(t("rules.save_changes"), type="primary", key="rules_grid_save"):
            _by_id = {r.id: r for r in rules}
            _n_changed = 0
            for _i in range(len(_page_df)):
                _old, _new = _page_df.iloc[_i], _edited_rules.iloc[_i]
                _changes = [c for c in (_col_cs, t("rules.col_context"), t("rules.col_priority"))
                            if str(_old[c]) != str(_new[c])]
                if not _changes:
                    continue
                _r = _by_id[int(_old["ID"])]
                _target = _new[_col_cs]
                _giro, _multi = _target == _giro_v, _target == _multi_v
                _cat, _sub = ("", "") if (_giro or _multi) else split_cat_sub(_target)
                # A rule keeps its direction: a spending rule never takes an
                # income category, nor the other way round.
                _was_income = _r.category in taxonomy.all_income_categories
                _was_expense = _r.category in taxonomy.all_expense_categories
                if _cat and ((_was_expense and _cat not in taxonomy.all_expense_categories)
                             or (_was_income and _cat not in taxonomy.all_income_categories)):
                    st.error(t("counterparts.wrong_direction", name=_r.pattern, cat=_cat))
                    continue
                _ctx = _new[t("rules.col_context")] or None
                rule_svc.update_rule(
                    _r.id, category=_cat, subcategory=_sub, context=_ctx,
                    priority=int(_new[t("rules.col_priority")] or 0),
                    is_giroconto=_giro, is_multi_category=_multi,
                )
                # The movements the rule covers follow it, as in the editor below.
                if _giro != bool(_r.is_giroconto):
                    tx_svc.set_giroconto_by_rule_pattern(_r.pattern, _r.match_type, make_giroconto=_giro,
                                                          direction=_r.direction)
                if not _giro and not _multi and _old[_col_cs] != _target:
                    for _tx in tx_svc.get_by_rule_pattern(_r.pattern, _r.match_type, _r.direction):
                        tx_svc.apply_rule_category(_tx.id, _cat, _sub, origin="rule_apply")
                _n_changed += 1
            if any(str(_page_df.iloc[_i][_col_cs]) != str(_edited_rules.iloc[_i][_col_cs])
                   and _multi_v in (str(_page_df.iloc[_i][_col_cs]), str(_edited_rules.iloc[_i][_col_cs]))
                   for _i in range(len(_page_df))):
                rule_svc.apply_to_all()
            if _n_changed:
                st.success(t("rules.grid_saved", n=_n_changed))
                st.rerun()
            else:
                st.info(t("ledger.no_changes"))
        st.caption(t("rules.rows_info", start=_start + 1, end=min(_end, len(df_rules)), total=len(df_rules)))

        # ── Esegui tutte le regole ───────────────────────────────────────
        st.divider()
        st.subheader(t("rules.run_all_title"))
        st.caption(t("rules.run_all_caption"))
        rc1, rc2 = st.columns([3, 2])
        with rc1:
            _run_confirm = st.checkbox(
                t("rules.run_all_confirm"),
                key="run_all_rules_confirm",
            )
        with rc2:
            if st.button(
                t("rules.run_all_btn"),
                type="primary",
                disabled=not _run_confirm,
                key="run_all_rules_btn",
                use_container_width=True,
            ):
                _n_matched, _n_cleared = rule_svc.apply_to_all()
                st.success(t("rules.run_all_done", matched=_n_matched, cleared=_n_cleared))
                logger.info(
                    f"rules_page: apply_all_rules matched={_n_matched} cleared={_n_cleared}"
                )
                st.rerun()

        st.divider()

        # ── Modifica / Elimina regola ────────────────────────────────────
        st.subheader(t("rules.edit_delete_title"))

        def _rule_target(r) -> str:
            if r.is_giroconto:
                return t("rules.giroconto_value")
            if r.is_multi_category:
                return t("rules.multi_value")
            return f"{r.category} / {r.subcategory or '-'}"

        rule_opts = {f"[{r.id}] {r.pattern[:60]}  →  {_rule_target(r)}": r
                     for r in sorted(rules, key=lambda x: x.pattern.casefold())}
        selected_label = st.selectbox(t("rules.select_rule"), list(rule_opts.keys()),
                                      key="rules_select")
        sel_rule = rule_opts[selected_label]

        col_edit, col_del = st.columns([3, 1])

        with col_edit:
            with st.expander(t("rules.edit_expander"), expanded=False):
                rid = sel_rule.id
                new_pattern = st.text_input(t("ledger.rule.pattern"), value=sel_rule.pattern,
                                            key=f"rule_edit_pattern_{rid}")
                _edit_label = label_from_type.get(sel_rule.match_type, list(match_labels.keys())[0])
                _edit_labels = list(match_labels.keys())
                new_match_label = st.selectbox(t("rules.match_type"), _edit_labels,
                                         index=_edit_labels.index(_edit_label),
                                         key=f"rule_edit_match_{rid}")
                new_match = match_labels[new_match_label]
                new_giro = st.checkbox(t("rules.giroconto_check"), value=sel_rule.is_giroconto,
                                       help=t("rules.giroconto_help"),
                                       key=f"rule_edit_giro_{rid}")
                if new_giro:
                    new_cat, new_sub = "", ""
                else:
                    new_cat, new_sub = cat_sub_select(
                        t("ledger.col.cat_sub"), taxonomy, sel_rule.category, sel_rule.subcategory,
                        key=f"rule_edit_cat_sub_{rid}",
                    )
                _cur_ctx = sel_rule.context or _NO_CONTEXT
                _ctx_idx = _ctx_options.index(_cur_ctx) if _cur_ctx in _ctx_options else 0
                new_ctx_raw = st.selectbox(
                    t("rules.context_optional"), _ctx_options, index=_ctx_idx,
                    key=f"rule_edit_ctx_{rid}",
                    help=t("rules.context_help"),
                )
                new_ctx = None if new_ctx_raw == _NO_CONTEXT else new_ctx_raw
                new_prio = st.number_input(t("rules.priority"), value=int(sel_rule.priority or 0),
                                           min_value=0, max_value=100, step=1,
                                           key=f"rule_edit_prio_{rid}")

                affected = tx_svc.get_by_rule_pattern(sel_rule.pattern, sel_rule.match_type,
                                                     sel_rule.direction)
                n_affected = len(affected)
                also_fix_txs = st.checkbox(
                    t("rules.also_update_txs", n=n_affected),
                    value=(n_affected > 0),
                    disabled=(n_affected == 0),
                    key=f"rule_edit_fix_txs_{rid}",
                )

                if st.button(t("rules.save_changes"), type="primary", key=f"rule_edit_save_{rid}"):
                    ok = rule_svc.update_rule(
                        sel_rule.id,
                        pattern=new_pattern,
                        match_type=new_match,
                        category=new_cat,
                        subcategory=new_sub,
                        context=new_ctx,
                        priority=new_prio,
                        is_giroconto=new_giro,
                    )
                    if ok and also_fix_txs and n_affected > 0:
                        if new_giro or sel_rule.is_giroconto:
                            tx_svc.set_giroconto_by_rule_pattern(
                                new_pattern, new_match, make_giroconto=new_giro,
                                direction=sel_rule.direction,
                            )
                        if not new_giro:
                            for tx in affected:
                                tx_svc.apply_rule_category(tx.id, new_cat, new_sub, origin="rule_apply")
                                if new_ctx:
                                    tx_svc.update_context(tx.id, new_ctx)
                    if ok:
                        msg = t("rules.rule_updated")
                        if also_fix_txs and n_affected > 0:
                            ctx_note = f" · {t('rules.col_context').lower()} '{new_ctx}'" if new_ctx else ""
                            msg += " " + t("rules.txs_recalculated", n=n_affected) + ctx_note + "."
                        st.success(msg)
                        logger.info(f"rules_page: updated rule {sel_rule.id}")
                        st.rerun()
                    else:
                        st.error(t("rules.rule_not_found"))

        with col_del:
            st.write("")
            st.write("")
            del_affected = tx_svc.get_by_rule_pattern(sel_rule.pattern, sel_rule.match_type,
                                                     sel_rule.direction)
            n_del = len(del_affected)

            if n_del > 0:
                st.warning(t("rules.del_warning", n=n_del))

            confirm_del = st.checkbox(t("rules.confirm_delete"), key=f"rule_del_confirm_{rid}")
            if st.button(t("rules.delete_btn"), type="secondary", key=f"rule_del_btn_{rid}",
                         disabled=not confirm_del):
                ok = rule_svc.delete_rule(sel_rule.id)
                if ok:
                    st.success(t("rules.rule_deleted"))
                    logger.info(f"rules_page: deleted rule {sel_rule.id}")
                    st.rerun()
                else:
                    st.error(t("rules.rule_not_found"))

    # ── Nomi tuoi (spec 27) ──────────────────────────────────────────────────
    _own = rule_svc.get_own_names()
    if _own:
        st.divider()
        st.subheader(t("rules.own_names_title", n=len(_own)))
        st.caption(t("rules.own_names_caption"))
        for _o in _own:
            _c_txt, _c_del = st.columns([6, 1])
            with _c_txt:
                st.markdown(t("rules.own_names_row", name=_o["name"],
                              amount=(f"{_o['amount']:.2f}" if _o["amount"] is not None
                                      else t("rules.own_names_variable")),
                              n=_o["n"], example=(_o["example"] or _o["signature"])[:90]))
            with _c_del:
                if st.button(t("rules.delete_btn"), key=f"own_name_del_{_o['id']}"):
                    rule_svc.delete_description_rule(_o["id"])
                    st.rerun()

    # ── Nuova regola manuale ─────────────────────────────────────────────────
    st.divider()
    st.subheader(t("rules.new_rule_title"))

    nr_pattern = st.text_input(t("rules.pattern_label"),
                               placeholder=t("rules.pattern_placeholder"),
                               key="new_rule_pattern")
    _nr_match_label = st.selectbox(t("rules.match_type"), list(match_labels.keys()), key="new_rule_match")
    nr_match = match_labels[_nr_match_label]
    nr_giro = st.checkbox(t("rules.giroconto_check"), help=t("rules.giroconto_help"),
                          key="new_rule_giro")
    if nr_giro:
        nr_cat, nr_sub = "", ""
    else:
        nr_cat, nr_sub = cat_sub_select(
            t("ledger.col.cat_sub"), taxonomy, None, None, key="new_rule_cat_sub",
        )
    nr_ctx_raw = st.selectbox(
        t("rules.context_optional"), _ctx_options, index=0, key="new_rule_ctx",
        help=t("rules.context_help"),
    )
    nr_ctx = None if nr_ctx_raw == _NO_CONTEXT else nr_ctx_raw
    nr_prio = st.number_input(t("rules.priority"), value=10, min_value=0, max_value=100, step=1,
                              key="new_rule_prio")

    # ── Preview: count matching transactions ─────────────────────────────────
    _nr_preview_txs: list = []
    _nr_preview_count = 0
    if nr_pattern.strip():
        try:
            # Only the movements in the rule's direction (spec 18): the
            # category's type tells it; a giroconto covers both ways.
            _nr_dir = (None if nr_giro else
                       "out" if nr_cat in taxonomy.all_expense_categories else
                       "in" if nr_cat in taxonomy.all_income_categories else None)
            _nr_preview_txs = tx_svc.get_by_rule_pattern(nr_pattern.strip(), nr_match, _nr_dir)
            _nr_preview_count = len(_nr_preview_txs)
            if _nr_preview_count > 0:
                st.info(t("rules.preview_match", n=_nr_preview_count))
            else:
                st.caption(t("rules.preview_no_match"))
        except Exception:
            st.warning(t("rules.preview_invalid_regex"))
            _nr_preview_txs = []

    nr_also_apply = False
    if _nr_preview_count > 0:
        nr_also_apply = st.checkbox(
            t("rules.new_rule_also_apply", n=_nr_preview_count),
            value=True,
            key="new_rule_also_apply",
        )

    if st.button(t("rules.create_btn"), type="primary", key="new_rule_submit"):
        if not nr_pattern.strip():
            st.error(t("rules.pattern_empty"))
        else:
            _, created = rule_svc.create_rule(
                pattern=nr_pattern.strip(),
                match_type=nr_match,
                category=nr_cat,
                subcategory=nr_sub,
                context=nr_ctx,
                priority=nr_prio,
                is_giroconto=nr_giro,
            )
            if nr_giro:
                nr_cat = t("rules.giroconto_value")
            ctx_label = f" · {t('rules.col_context').lower()}: {nr_ctx}" if nr_ctx else ""
            if created:
                st.success(t("rules.created", pattern=nr_pattern, cat=nr_cat, sub=nr_sub) + ctx_label)
                logger.info(f"rules_page: created rule pattern={nr_pattern!r} cat={nr_cat!r} ctx={nr_ctx!r}")
            else:
                st.warning(t("rules.existing_updated", pattern=nr_pattern, cat=nr_cat, sub=nr_sub) + ctx_label)
                logger.info(f"rules_page: updated existing rule pattern={nr_pattern!r} cat={nr_cat!r} ctx={nr_ctx!r}")
            if nr_also_apply and _nr_preview_txs and nr_giro:
                tx_svc.set_giroconto_by_rule_pattern(
                    nr_pattern.strip(), nr_match, make_giroconto=True,
                )
            elif nr_also_apply and _nr_preview_txs:
                for _tx in _nr_preview_txs:
                    tx_svc.apply_rule_category(_tx.id, nr_cat, nr_sub, origin="rule_apply")
                    if nr_ctx:
                        tx_svc.update_context(_tx.id, nr_ctx)
                logger.info(
                    f"rules_page: applied new rule to {len(_nr_preview_txs)} existing transactions"
                    f" (pattern={nr_pattern!r})"
                )
            st.rerun()
