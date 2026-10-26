"""Spendif.ai – Streamlit entrypoint (RF-08).

Pages:
  📥 Import             – upload + pipeline processing
  📜 Import History     – import timeline with undo
  📋 Ledger             – filterable transaction table + export
  🧾 Confirm expense    - a receipt in hand: date and amount, then the category
  ✏️ Bulk Edit          – bulk edits: category, context, deletion, duplicates
  📊 Analytics          – interactive charts (Plotly)
  📋 Report             – spending by context/category with pivot, trends, Excel export
  💰 Budget             – define % budget targets per category
  📊 Budget vs Actual   – compare actual spending vs budget targets
  🔍 Review             – manual review of low-confidence items
  📏 Rules              – manage category rules (edit / delete / create)
  🏪 Counterparts       – per-vendor stats grid with inline rule creation
  🗂️ Taxonomy           – manage categories and subcategories
  ⚙️ Settings           – locale, language, LLM backend preferences
  ✅ Checklist          – monthly tx presence per account (pivot table)
  💬 Chat              – adaptive support chatbot (RAG cloud/local or FAQ match)
"""
import os
from pathlib import Path

from dotenv import load_dotenv
# Bundled installs (Linux .deb/.rpm) ship the app under /opt/spendifai which is
# read-only — the launcher writes the user-specific .env to ~/.spendifai/.env.
# Load that first; load_dotenv() does NOT override existing env vars, so the
# desktop launcher's os.environ wins and a cwd-local .env (dev mode) acts as a
# fallback for keys neither parent set.
load_dotenv(Path.home() / ".spendifai" / ".env")
load_dotenv()

import streamlit as st

from db.models import create_tables, get_engine
from support.logging import capture_unhandled_exceptions, setup_logging

logger = setup_logging()
capture_unhandled_exceptions()
logger.info("Starting Spendif.ai")

# The server never outlives the desktop launcher (support/parent_watchdog.py):
# once per process, a no-op when run without the launcher.
from support.parent_watchdog import start as _watch_launcher

_watch_launcher()

# ── S-01: Prompt integrity check ─────────────────────────────────────────────
from core.prompt_guard import verify_prompt_integrity

_prompt_errors = verify_prompt_integrity()
if _prompt_errors:
    # Shown before i18n is loaded — hardcoded bilingual warning
    st.error(
        "**LLM prompts modified from certified version / Prompt LLM modificati rispetto alla versione certificata.**\n\n"
        + "\n".join(f"- {e}" for e in _prompt_errors)
        + "\n\nRun / Eseguire: `python tools/compute_prompt_hashes.py`"
    )

# ── DB bootstrap ──────────────────────────────────────────────────────────────
DB_URL = os.getenv("SPENDIFAI_DB", "sqlite:///ledger.db")
engine = create_tables(get_engine(DB_URL))

# Payment intermediaries shipped with the app (spec 20): an update can add some.
from services.intermediary_service import sync_seed_once

sync_seed_once(engine)

# ── Dev mode (AI-193) ─────────────────────────────────────────────────────────
# When SPENDIFAI_DEV_MODE=1 the developer-only pages (e.g. the pipeline
# Debugger) become reachable. Never set in production builds.
_DEV_MODE = os.getenv("SPENDIFAI_DEV_MODE") == "1"

# ── Startup cleanup ───────────────────────────────────────────────────────────
# Once per process, not per browser session: a second tab during an import
# must not declare that import interrupted.
from services.import_service import reset_stale_jobs_once

_n_stale = reset_stale_jobs_once(engine)
if _n_stale:
    logger.info(f"startup: reset {_n_stale} stale running job(s) to error")

# Installations onboarded before 2026-10-23 analyse on the processor.
from services.llm_service import restore_accelerator_layers

_layers = restore_accelerator_layers(engine)
if _layers:
    logger.info(f"startup: accelerator layers restored for {', '.join(_layers)}")

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Spendif.ai",
    layout="wide",
    page_icon="🏦",
)

# ── Compact layout: reduce top margin and padding ────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1rem !important; padding-bottom: 0.5rem !important; }
    header[data-testid="stHeader"] { height: 2rem !important; }
    /* Need a bit of breathing room at the top of the main container,
       otherwise tall glyphs and emojis (e.g. the 📨 in "📨 Import")
       get clipped because Streamlit ships overflow:hidden on the
       block container. */
    .stMainBlockContainer { padding-top: 1.25rem !important; }
    h1, h2, h3 {
        margin-top: 0.5rem !important;
        margin-bottom: 0.4rem !important;
        line-height: 1.35 !important;
    }
    .stDataFrame, .stDataEditor { margin-top: 0.2rem !important; }
    div[data-testid="stExpander"] { margin-top: 0.3rem !important; }
</style>
""", unsafe_allow_html=True)

# ── First-run experience ─────────────────────────────────────────────────────
# Before the first onboarding the user should see ONE focused page — no
# sidebar, no toolbar — that combines the live model-download status and
# the configuration wizard. As soon as onboarding is done we drop back to
# the normal layout. If a model download is still in flight at that point,
# the wizard has already completed and the user opted in to using the rest
# of the app (Import is gated separately).
from services.settings_service import SettingsService as _SvcCheck
_cfg_check = _SvcCheck(engine)

# `onboarding_done` is flipped at the end of step 5 (Conferma) so that
# closing the app on step 6 (Primo Import) does NOT replay the wizard at
# next launch. We keep rendering the wizard while the session is still on
# step 6, so the user can finish choosing between "upload now" vs "skip".
_in_step6 = st.session_state.get("_ob_step") == 6
if not _cfg_check.is_onboarding_done() or _in_step6:
    # Hide chrome: full-screen immersive look while onboarding.
    from ui.widgets.immersive import hide_chrome
    hide_chrome()

    # Live download status (banner-only, sits above the wizard).
    from ui.widgets.model_download_banner import render_model_download_banner
    render_model_download_banner()

    from ui.onboarding_page import render_onboarding_page
    render_onboarding_page(engine)
    st.stop()

# ── Normal-mode banner ───────────────────────────────────────────────────────
# Once onboarding is done, the user can roam the app. If the model is still
# downloading in the background, show the same live banner at the top of
# every page (sidebar visible). Renders nothing when no download is active.
from ui.widgets.model_download_banner import render_model_download_banner
render_model_download_banner()

# ── i18n: set UI language from user settings ─────────────────────────────────
from ui.i18n import set_language as _set_lang
_set_lang(_cfg_check.get_all().get("ui_language", "en"))

# ── Update check ─────────────────────────────────────────────────────────────
# Once per session: record which build is running (update_event table) and ask
# GitHub in the background whether a newer release exists. The banner is built
# from STORED state, so nothing here waits on the network. The sidebar stays
# engine-free, so the answer travels through session_state like the default
# page above.
if "update_check_done" not in st.session_state:
    st.session_state["update_check_done"] = True
    from services.update_service import UpdateService
    _upd_svc = UpdateService(engine)
    _upd_svc.record_launch()
    _upd_svc.start_background_check()
    st.session_state["update_banner"] = _upd_svc.pending_update()

# ── Sidebar navigation ────────────────────────────────────────────────────────
# Default landing page is data-driven: with at least one transaction we go
# straight to the Home dashboard; on an empty DB we land on Import (whose
# empty state nudges the user to upload). Computed once here so sidebar
# can stay engine-free.
# The menu at the user's level (spec 19), computed here for the same reason:
# every run, since the level grows with the data and the Settings can change
# it.
from services.user_level_service import UserLevelService as _LevelSvc
from ui.sidebar import MENU as _MENU, NAV_PAGES as _NAV_PAGES, render_sidebar

st.session_state[_MENU] = _LevelSvc(engine).menu(_NAV_PAGES)
if "page" not in st.session_state:
    from services.transaction_service import TransactionService as _TxSvc
    # An empty DB lands on the page that asks how to start (spec 16, 2).
    st.session_state["page"] = "home" if _TxSvc(engine).has_transactions() else "start"

page = render_sidebar()

# ── Route ─────────────────────────────────────────────────────────────────────
def _route(page: str, engine) -> None:
    """Render the selected page.

    A function rather than a bare chain so there is one place to catch what a
    page raises. Streamlit renders an uncaught exception on screen and does not
    put it in the application log: on 2026-09-23 the traceback of the broken
    Chat page existed in exactly one file, the launcher log, and that file is
    rewritten at every start. The technical report for support collects the
    application log, so it arrived empty on precisely the defect it was built
    to describe.
    """
    if page == "home":
        from ui.home_page import render_home_page
        render_home_page(engine)

    elif page == "import":
        from ui.upload_page import render_upload_page
        render_upload_page(engine)

    elif page == "history":
        from ui.history_page import render_history_page
        render_history_page(engine)

    elif page == "ledger":
        from ui.registry_page import render_registry_page
        render_registry_page(engine)

    elif page == "expense_confirm":
        from ui.expense_confirm_page import render_expense_confirm_page
        render_expense_confirm_page(engine)

    elif page == "bulk_edit":
        from ui.bulk_edit_page import render_bulk_edit_page
        render_bulk_edit_page(engine)

    elif page == "analytics":
        from ui.analysis_page import render_analysis_page
        render_analysis_page(engine)

    elif page == "report":
        from ui.report_page import render_report_page
        render_report_page(engine)

    elif page == "mixed":
        from ui.mixed_page import render_mixed_page
        render_mixed_page(engine)

    elif page == "transfers":
        from ui.transfers_page import render_transfers_page
        render_transfers_page(engine)

    elif page == "budget":
        from ui.budget_page import render_budget_page
        render_budget_page(engine)

    elif page == "budget_vs_actual":
        from ui.budget_vs_actual_page import render_budget_vs_actual_page
        render_budget_vs_actual_page(engine)

    elif page == "review":
        from ui.review_page import render_review_page
        render_review_page(engine)

    elif page == "rules":
        from ui.rules_page import render_rules_page
        render_rules_page(engine)
    elif page == "intermediaries":
        from ui.intermediaries_page import render_intermediaries_page
        render_intermediaries_page(engine)

    elif page == "counterparts":
        from ui.counterparts_page import render_counterparts_page
        render_counterparts_page(engine)

    elif page == "income":
        from ui.counterparts_page import render_income_page
        render_income_page(engine)

    elif page == "taxonomy":
        from ui.taxonomy_page import render_taxonomy_page
        render_taxonomy_page(engine)

    elif page == "llm_models":
        from ui.llm_models_page import render_llm_models_page
        render_llm_models_page(engine)

    elif page == "settings":
        from ui.settings_page import render_settings_page
        render_settings_page(engine)

    elif page == "diagnostics":
        from ui.diagnostics_page import render_diagnostics_page
        render_diagnostics_page(engine)

    elif page == "checklist":
        from ui.checklist_page import render_checklist_page
        render_checklist_page(engine)

    elif page == "check":
        from ui.checking_page import render_checking_page
        render_checking_page(engine)

    elif page == "chat":
        from ui.chat_page import render_chat_page
        render_chat_page(engine)

    elif page == "start":
        from ui.start_page import render_start_page
        render_start_page(engine)

    elif page == "guided":
        # The workflow renders the regular pages through this same router.
        from ui.guided_page import render_guided_page
        render_guided_page(engine, _route)

    elif _DEV_MODE and page == "debugger":
        from ui.debugger_page import render_debugger_page
        render_debugger_page(engine)


try:
    _route(page, engine)
except Exception:
    # Logged, then re-raised: Streamlit still shows the error on screen, the
    # log keeps the only copy that survives the restart.
    logger.exception("Unhandled exception while rendering page '%s'", page)
    raise
