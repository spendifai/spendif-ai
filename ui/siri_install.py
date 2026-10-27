"""Adding the Siri command, from the Settings and from the offer at start
(spec 32, 4.9). On a Mac only: the app builds and signs the command, and
Shortcuts asks the user to add it with one click."""
from __future__ import annotations

import streamlit as st

from services.expense_confirm_service import ExpenseConfirmService
from services.settings_service import SettingsService
from ui.i18n import get_language, t


def render_phone_folder(cfg: SettingsService, settings: dict, key: str) -> None:
    """Where the expenses from the iPhone app are read, on every desktop
    system (spec 34, 4): empty for the default of this system, or a folder
    shared between accounts or synced by other means. Saved as typed.

    Args:
        cfg: the settings service, to save the folder.
        settings: the current settings, as SettingsService.get_all returns.
        key: a prefix for the widget keys, one per page that draws this.
    """
    st.subheader(t("settings.siri.title"))
    app_folder = st.text_input(t("settings.siri.app_folder"), value=settings.get("siri_app_folder") or "",
                               help=t("settings.siri.app_folder_help"), key=f"{key}_siri_app_folder").strip()
    if app_folder != (settings.get("siri_app_folder") or ""):
        cfg.set("siri_app_folder", app_folder)


def render_siri_install(cfg: SettingsService, settings: dict, key: str) -> bool:
    """The folder name and the button that adds the command, on a Mac.
    Returns True once the command was opened in Shortcuts.

    Args:
        cfg: the settings service, to save the folder and that the command
            is installed.
        settings: the current settings, as SettingsService.get_all returns.
        key: a prefix for the widget keys, one per page that draws this.
    """
    folder = st.text_input(t("settings.siri.folder"), value=settings.get("siri_folder") or "Spendif.ai",
                           help=t("settings.siri.folder_help"), key=f"{key}_siri_folder").strip() or "Spendif.ai"
    name = st.text_input(t("settings.siri.name"), value=settings.get("siri_name") or ExpenseConfirmService.siri_default_name(get_language()),
                         help=t("settings.siri.name_help"), key=f"{key}_siri_name").strip() or ExpenseConfirmService.siri_default_name(get_language())
    installed = settings.get("siri_enabled") == "true"
    if installed and (folder != (settings.get("siri_folder") or "Spendif.ai")
                      or name != (settings.get("siri_name") or ExpenseConfirmService.siri_default_name(get_language()))):
        st.caption(t("settings.siri.reinstall"))
    st.caption(t("settings.siri.privacy"))
    if st.button(t("settings.siri.install"), key=f"{key}_siri_install", type="primary"):
        try:
            ExpenseConfirmService.install_siri_shortcut(folder, get_language(), name)
        except Exception as exc:                 # the signing tool may be missing or refuse
            st.error(t("settings.siri.failed", error=str(exc)[:200]))
            return False
        cfg.set("siri_folder", folder)
        cfg.set("siri_name", name)
        cfg.set("siri_enabled", "true")
        st.success(t("settings.siri.opened", name=name.split(",")[0].strip()))
        return True
    st.caption(t("settings.siri.not_found"))
    st.caption(t("settings.siri.no_phrase"))
    return False


def render_siri_offer(engine) -> None:
    """Once, on a Mac, at start: would the user like to tell expenses to
    Siri, even with the app closed? Never opens anything without a click."""
    cfg = SettingsService(engine)
    settings = cfg.get_all()
    if settings.get("siri_enabled") == "true" or settings.get("siri_offer_seen") == "true":
        return
    with st.container(border=True):
        st.write(t("siri_offer.text"))
        c_yes, c_no = st.columns(2)
        if c_yes.button(t("siri_offer.yes"), key="siri_offer_yes", type="primary", width="stretch"):
            try:
                ExpenseConfirmService.install_siri_shortcut(settings.get("siri_folder") or "Spendif.ai",
                                                            get_language(), settings.get("siri_name") or "")
                cfg.set("siri_enabled", "true")
                cfg.set("siri_folder", settings.get("siri_folder") or "Spendif.ai")
                cfg.set("siri_offer_seen", "true")
                st.success(t("settings.siri.opened", name=settings.get("siri_name") or ExpenseConfirmService.siri_default_name(get_language())))
            except Exception as exc:
                st.error(t("settings.siri.failed", error=str(exc)[:200]))
        if c_no.button(t("siri_offer.no"), key="siri_offer_no", width="stretch"):
            cfg.set("siri_offer_seen", "true")
            st.rerun()
