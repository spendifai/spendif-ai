"""Live banner that surfaces the LLM model download status to the user.

The desktop launcher (``desktop/launcher.py``) downloads the GGUF model in
a background thread and writes ``~/.spendifai/model_download.status`` as
JSON. This widget polls that file via ``st.fragment(run_every=2)`` and
displays a progress banner with an ETA. The fragment refreshes only its
own subtree, so the rest of the Streamlit page (wizard, ledger, etc.)
does not flicker.

Status file schema (written atomically by the launcher):

    {
      "pct": 0.42,                  # 0.0..1.0
      "msg": "Scaricando...",       # human-readable phase
      "elapsed_s": 73,              # seconds since start
      "eta_remaining_s": 95,        # seconds remaining, null until 2% reached
      "done": false,                # true when finished or skipped
      "error": "...",               # non-null on failure
      "ts": 1747396800.0            # wall-clock timestamp of write
    }

When the file is absent or ``done`` is true and the model is present, the
banner renders nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st
from ui.i18n import t


_STATUS_FILE = Path.home() / ".spendifai" / "model_download.status"
# Mirror desktop/launcher.py's _LOG_FILE so the user-facing hint points to the
# right place on every OS (macOS keeps Apple's convention, others use the dot
# directory under $HOME).
_LAUNCHER_LOG_PATH = (
    "~/Library/Logs/spendifai-launcher.log"
    if sys.platform == "darwin"
    else "~/.spendifai/spendifai-launcher.log"
)


def _load_status() -> dict | None:
    if not _STATUS_FILE.exists():
        return None
    try:
        return json.loads(_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "in calcolo..."
    if seconds < 60:
        return f"{seconds} sec"
    if seconds < 3600:
        m = seconds // 60
        return f"~{m} min"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"~{h}h {m}min"


@st.fragment(run_every=2)  # type: ignore[attr-defined]
def render_model_download_banner() -> None:
    """Render a live download banner. Auto-refreshes every 2 seconds.

    The fragment only re-renders its own subtree, so wizard input fields
    keep their state across refreshes.
    """
    status = _load_status()
    if status is None or status.get("done"):
        # Nothing to show — either no download is in progress, or it
        # completed (the launcher cleans up via "done": true).
        return

    error = status.get("error")
    if error:
        st.error(
            t("model_banner.failed", error=error, log=_LAUNCHER_LOG_PATH)
        )
        return

    pct = float(status.get("pct", 0.0))
    msg = status.get("msg", "Preparazione...")
    eta_str = _format_duration(status.get("eta_remaining_s"))
    pct_int = int(pct * 100)

    st.info(
        t("model_banner.downloading_full", pct=pct_int, eta=eta_str, msg=msg)
    )
    st.progress(pct, text=f"{pct_int}%")
