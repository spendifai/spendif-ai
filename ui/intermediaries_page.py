"""Payment intermediaries page, for experts (spec 20).

PayPal, SumUp and the like: the counterpart of a movement paid through them
is the merchant after the asterisk. The table grows from the list shipped
with the app, from the user's movements and by hand; here it is seen,
switched on and off, and lengthened.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from services.intermediary_service import IntermediaryService
from ui.i18n import t


def _origin_label(origin: str) -> str:
    return t(f"intermediaries.origin.{origin}") if origin in ("seed", "learned", "user") else origin


def render_intermediaries_page(engine) -> None:
    st.header(t("intermediaries.title"))
    st.caption(t("intermediaries.caption"))
    svc = IntermediaryService(engine)

    rows = svc.page_rows()
    col_prefix = t("intermediaries.col_prefix")
    col_origin = t("intermediaries.col_origin")
    col_active = t("intermediaries.col_active")
    col_moves = t("intermediaries.col_movements")
    col_examples = t("intermediaries.col_examples")
    df = pd.DataFrame([
        {col_prefix: r["prefix"], col_origin: _origin_label(r["origin"]),
         col_active: r["active"], col_moves: r["movements"],
         col_examples: ", ".join(r["examples"])}
        for r in rows
    ], columns=[col_prefix, col_origin, col_active, col_moves, col_examples])

    edited = st.data_editor(
        df,
        column_config={
            col_prefix: st.column_config.TextColumn(col_prefix, disabled=True),
            col_origin: st.column_config.TextColumn(col_origin, disabled=True),
            col_active: st.column_config.CheckboxColumn(col_active, help=t("intermediaries.active_help")),
            col_moves: st.column_config.NumberColumn(col_moves, disabled=True),
            col_examples: st.column_config.TextColumn(col_examples, disabled=True, width="large"),
        },
        hide_index=True, num_rows="fixed", width="stretch", key="intermediaries_editor",
    )
    changed = [
        (df.at[i, col_prefix], bool(edited.at[i, col_active]))
        for i in range(len(df)) if bool(edited.at[i, col_active]) != bool(df.at[i, col_active])
    ]
    if changed and st.button(t("intermediaries.save_btn"), type="primary", key="intermediaries_save"):
        for prefix, active in changed:
            svc.set_active(prefix, active)
        st.toast(t("intermediaries.saved", n=len(changed)), icon="✅")
        st.rerun()
    st.caption(t("intermediaries.deactivate_note"))

    st.divider()
    st.subheader(t("intermediaries.add_title"))
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    new = c1.text_input(t("intermediaries.add_label"), key="intermediaries_new",
                        help=t("intermediaries.add_help"))
    if c2.button(t("intermediaries.add_btn"), key="intermediaries_add", disabled=not new.strip()):
        prefix = svc.add_user(new)
        if prefix:
            st.toast(t("intermediaries.added", prefix=prefix), icon="✅")
        st.rerun()
