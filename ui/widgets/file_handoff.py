"""Handing a file to the person using the application.

WHY IT EXISTS
    `st.download_button` renders an `<a download>`. The desktop window is a
    WKWebView, which does not honour that attribute without a download
    delegate, and pywebview 6.2.1 has none: its events are closed, closing,
    loaded, before_load, before_show, initialized, shown, minimized,
    maximized, restored, resized, moved, request_sent and response_received,
    and not one of them is about a download.

    So the window navigates to the file instead of saving it. Reported on
    2026-09-25 against 0.3.1: the technical report appeared on screen as a
    wall of values with the tags stripped, nothing was saved, and there is no
    back button in that window, so the only way out was to restart.

    Every export in the product went through that button, which means none of
    them ever worked in any package: the ledger in two formats, the report,
    the analyses, the checklist, the technical report. It runs in development
    because a real browser is doing the work there.

WHAT IT DOES INSTEAD
    In a packaged build the file is written from Python, which owns a real
    filesystem, and the path is shown. In a browser the original button is the
    right thing and stays.

    The same reasoning covers `mailto:` links, which that window swallows in
    the same silence: the mail client is opened by the operating system, from
    here, rather than by a link the webview will not follow.
"""

from __future__ import annotations

import sys
import urllib.parse
import webbrowser
from pathlib import Path

import streamlit as st

from ui.i18n import t


def is_packaged() -> bool:
    """True inside the frozen desktop bundle, where the window is a webview."""
    return bool(getattr(sys, "frozen", False))


def _destination() -> Path:
    """Downloads, because that is where a person goes to look for a file.

    Falls back to the home directory rather than failing: a missing Downloads
    folder is unusual but not a reason to lose the document.
    """
    downloads = Path.home() / "Downloads"
    try:
        downloads.mkdir(parents=True, exist_ok=True)
        return downloads
    except OSError:
        return Path.home()


def _free_path(directory: Path, file_name: str) -> Path:
    """A path that does not overwrite anything.

    Exports have fixed names (spendifai_export.csv), so the second export of
    the day would silently replace the first one.
    """
    candidate = directory / file_name
    if not candidate.exists():
        return candidate

    stem, suffix = candidate.stem, candidate.suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"no free name for {file_name}")


def save_file(data: str | bytes, file_name: str) -> Path:
    """Write the file and return where it landed."""
    path = _free_path(_destination(), file_name)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def offer_file(
    label: str,
    data: str | bytes,
    file_name: str,
    mime: str,
    *,
    key: str | None = None,
    use_container_width: bool = False,
) -> Path | None:
    """Offer one file, by whichever route works where the page is running.

    Returns the path once it has been written, on this run and on the reruns
    after it, so a caller can say something more after the file exists.
    """
    state_key = f"file_handoff:{key or file_name}"

    if not is_packaged():
        st.download_button(
            label, data, file_name, mime, use_container_width=use_container_width
        )
        return None

    if st.button(label, key=f"{state_key}:button", use_container_width=use_container_width):
        try:
            st.session_state[state_key] = str(save_file(data, file_name))
        except OSError as exc:
            st.session_state.pop(state_key, None)
            st.error(t("file.save_failed").format(error=exc))

    saved = st.session_state.get(state_key)
    if saved:
        st.success(t("file.saved").format(path=saved))
        return Path(saved)
    return None


def open_mail_client(address: str, subject: str, body: str) -> bool:
    """Ask the operating system to open a message, already written.

    A mailto: link inside the desktop window does nothing at all, for the same
    reason a download does nothing: the webview does not follow it. Going
    through the operating system works because the webview is not involved.

    No attachment: mailto cannot carry one. The body says where the file is,
    which is why this is offered after it has been saved and not before.
    """
    query = urllib.parse.urlencode(
        {"subject": subject, "body": body}, quote_via=urllib.parse.quote
    )
    try:
        return bool(webbrowser.open(f"mailto:{address}?{query}"))
    except Exception:  # noqa: BLE001 - never break a page over this
        return False
