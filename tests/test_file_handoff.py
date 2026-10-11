"""Handing a file over, in the window where the usual way does not work.

Reported on 2026-09-25 against 0.3.1: saving the technical report navigated
the desktop window to the document instead of writing it to disk, and left the
person on a page with no way back. Every export in the product used the same
button, so none of them worked in any package.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.widgets import file_handoff


@pytest.fixture(autouse=True)
def module_restored():
    """The page scripts below rebind these on the module itself.

    AppTest runs the script in this same process, so without this every test
    after them would inherit whichever route the last one asked for.
    """
    saved = (file_handoff.is_packaged, file_handoff._destination)
    yield
    file_handoff.is_packaged, file_handoff._destination = saved


@pytest.fixture()
def downloads(tmp_path, monkeypatch):
    target = tmp_path / "Downloads"
    target.mkdir()
    monkeypatch.setattr(file_handoff, "_destination", lambda: target)
    return target


def test_text_and_bytes_both_land_on_disk(downloads):
    text_path = file_handoff.save_file("<report/>", "report.xml")
    bytes_path = file_handoff.save_file(b"col\n1\n", "export.csv")

    assert text_path.read_text() == "<report/>"
    assert bytes_path.read_bytes() == b"col\n1\n"


def test_a_second_export_does_not_replace_the_first(downloads):
    """Exports have fixed names, so overwriting is the default outcome."""
    first = file_handoff.save_file("one", "spendifai_export.csv")
    second = file_handoff.save_file("two", "spendifai_export.csv")

    assert first != second
    assert first.read_text() == "one"
    assert second.name == "spendifai_export (2).csv"


def test_the_message_is_opened_through_the_operating_system(monkeypatch):
    """A mailto: link in that window is followed no more than a download is."""
    opened: list[str] = []
    monkeypatch.setattr(
        file_handoff.webbrowser, "open", lambda url: opened.append(url) or True
    )

    assert file_handoff.open_mail_client(
        "support@spendif.ai", "Spendif.ai 0.3.1", "it is here: /Users/x/Downloads/r.xml"
    )
    url = opened[0]
    assert url.startswith("mailto:support@spendif.ai?")
    assert "Spendif.ai%200.3.1" in url
    # The path travels in the body, because mailto cannot carry an attachment.
    assert "Downloads" in url


def test_a_mail_client_that_will_not_open_is_not_an_exception(monkeypatch):
    def explode(url):
        raise RuntimeError("no mail client")

    monkeypatch.setattr(file_handoff.webbrowser, "open", explode)
    assert file_handoff.open_mail_client("support@spendif.ai", "s", "b") is False


# ── The two routes, as a page actually renders them ──────────────────────────


def _offer_page(packaged: bool, destination: str) -> None:
    from pathlib import Path as _Path

    from ui.widgets import file_handoff as handoff

    handoff.is_packaged = lambda: packaged
    handoff._destination = lambda: _Path(destination)
    handoff.offer_file("Save it", "<report/>", "report.xml", "application/xml")


def _run(packaged: bool, destination: Path) -> AppTest:
    app = AppTest.from_function(
        _offer_page, kwargs={"packaged": packaged, "destination": str(destination)}
    )
    app.run(timeout=30)
    return app


def test_in_a_browser_the_ordinary_download_button_is_still_right(tmp_path):
    app = _run(packaged=False, destination=tmp_path)

    assert not app.exception
    assert app.get("download_button"), "the browser route lost its download button"
    assert not app.button


def test_in_the_packaged_window_nothing_relies_on_the_webview(tmp_path):
    app = _run(packaged=True, destination=tmp_path)

    assert not app.exception
    # The element that does not work in that window must not be on the page.
    assert not app.get("download_button")
    assert app.button, "no way to ask for the file at all"


def test_the_packaged_button_writes_the_file_and_says_where(tmp_path):
    app = _run(packaged=True, destination=tmp_path)
    app.button[0].click().run(timeout=30)

    written = list(tmp_path.glob("report.xml"))
    assert written, "the button did not write anything"
    assert written[0].read_text() == "<report/>"
    assert str(tmp_path) in " ".join(s.value for s in app.success)
