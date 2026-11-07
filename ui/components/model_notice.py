"""Notice shown when the import would run on a model we never tested (spec 36, 5).

In the first alpha test an untested reasoning model, left in the models folder
by an older version, ran every phase for hours and renamed counterparts, and
nothing in the app said so. The notice names the model, says what it risks in
plain words, and puts the recommended one a click away. It proposes, it never
switches on its own: "Keep mine" is remembered for those same files.

Usage:
    from ui.components.model_notice import render_model_notice
    render_model_notice(engine)   # at the top of the Home and Import pages
"""

from __future__ import annotations

import streamlit as st

from services.llm_service import (
    download_recommended_model,
    keep_untested_model,
    out_of_catalog_notice,
    use_recommended_model,
)
from services.settings_service import SettingsService
from ui.i18n import t


def render_model_notice(engine, key_prefix: str) -> None:
    """Show the out-of-catalog notice when an untested local model is in use.

    Args:
        engine: the SQLAlchemy engine of the ledger.
        key_prefix: prefix for the widget keys, distinct per page that shows
            the notice (two pages must not share a button key).
    """
    settings_svc = SettingsService(engine)
    notice = out_of_catalog_notice(settings_svc.get_all())
    if notice is None:
        return

    with st.container(border=True):
        st.warning(t(
            "model_notice.body",
            models=", ".join(notice.model_names),
            recommended=notice.recommended_name,
        ))
        use_col, keep_col = st.columns(2)
        with use_col:
            label = (
                t("model_notice.use", recommended=notice.recommended_name)
                if notice.recommended_path
                else t("model_notice.download_and_use", recommended=notice.recommended_name, size=notice.recommended_size_gb)
            )
            if st.button(label, type="primary", key=f"{key_prefix}_model_notice_use", use_container_width=True):
                model_path = notice.recommended_path or _download_with_progress()
                if model_path:
                    use_recommended_model(settings_svc, notice, model_path)
                    st.rerun()
        with keep_col:
            if st.button(t("model_notice.keep"), key=f"{key_prefix}_model_notice_keep", use_container_width=True):
                keep_untested_model(settings_svc, notice)
                st.rerun()


def _download_with_progress() -> str | None:
    """Download the recommended model with a progress bar; the file, or None on failure."""
    progress = st.progress(0.0, text=t("model_notice.downloading"))

    def on_progress(fraction: float, _message: str = "") -> None:
        progress.progress(max(0.0, min(1.0, fraction)), text=t("model_notice.downloading"))

    model_path = download_recommended_model(progress_callback=on_progress)
    if not model_path:
        st.error(t("model_notice.download_failed"))
    return model_path
