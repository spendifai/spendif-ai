"""The diagnostics page, executed rather than imported.

WHY IT EXISTS
    Until 2026-09-25 this page had been verified by importing it, by resolving
    its translation keys and by testing the service behind it. Nobody had ever
    run it. That is the same gap that shipped a Chat page which crashed on
    every installed build and a release whose saved context broke every import:
    the code was sound, the thing itself had not been opened.

    AppTest runs the page against a real Streamlit runtime, so an exception
    while rendering, a missing translation key or a table built from a column
    that is not there fails here instead of on somebody's machine.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from streamlit.testing.v1 import AppTest

from db.models import Base, ImportJob, LlmUsageLog

LEAKS = (
    "/Users/",
    "estratto_conto_amex_gennaio.xlsx",
    "PAGAMENTO CARTA ESSELUNGA MILANO",
)


def _page(db_url: str, with_history: bool, packaged: bool = False,
          destination: str | None = None) -> None:
    """The script AppTest executes. Runs in the runtime, not in the test."""
    from pathlib import Path as _Path

    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.orm import sessionmaker as _sessionmaker

    from db.models import Base as _Base, ImportJob as _Job, LlmUsageLog as _Usage
    from ui.widgets import file_handoff as _handoff
    from ui import diagnostics_page as _page_module
    from ui.diagnostics_page import render_diagnostics_page

    # Both names are rebound: the page imported one of them into its own
    # namespace, the helper reads the other as a module global.
    _handoff.is_packaged = lambda: packaged
    _page_module.is_packaged = lambda: packaged
    if destination:
        _handoff._destination = lambda: _Path(destination)

    engine = _create_engine(db_url)
    _Base.metadata.create_all(engine)

    if with_history:
        session = _sessionmaker(bind=engine)()
        session.add(_Job(status="completed", n_transactions=120, n_files=2,
                         ms_header_detection=2400, ms_categorizing=60000))
        session.add(_Usage(
            backend="local_llama_cpp",
            model_id="/Users/someone/.spendifai/models/gemma-3-12b.gguf",
            caller="categorizer",
            source_name="estratto_conto_amex_gennaio.xlsx",
            n_ctx=4096, prompt_tokens=4000, duration_ms=900,
        ))
        session.commit()
        session.close()

    render_diagnostics_page(engine)


@pytest.fixture()
def db_url(tmp_path):
    """A file-backed database: the page opens its own sessions on the engine."""
    url = f"sqlite:///{tmp_path / 'diagnostics.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def _run(db_url: str, with_history: bool, **kwargs) -> AppTest:
    app = AppTest.from_function(
        _page, kwargs={"db_url": db_url, "with_history": with_history, **kwargs}
    )
    app.run(timeout=60)
    return app


@pytest.fixture(autouse=True)
def module_restored():
    """The script rebinds these in this same process. Put them back."""
    from ui import diagnostics_page as page_module
    from ui.widgets import file_handoff as handoff

    saved = (handoff.is_packaged, handoff._destination, page_module.is_packaged)
    yield
    handoff.is_packaged, handoff._destination, page_module.is_packaged = saved


def test_the_page_renders_on_a_database_with_nothing_in_it(db_url):
    """First launch is the state every new user is in, and the emptiest path."""
    app = _run(db_url, with_history=False)

    assert not app.exception, f"the page raised: {app.exception}"
    # The three answers somebody asking for help needs first.
    assert len(app.metric) == 3
    assert app.subheader, "no section was drawn"


def test_the_page_renders_with_imports_and_model_calls_behind_it(db_url):
    app = _run(db_url, with_history=True)

    assert not app.exception, f"the page raised: {app.exception}"
    assert app.table, "the observed calls and the per-row timings drew no table"


def test_the_document_is_on_screen_before_it_can_be_saved(db_url):
    """The page promises it shows what it hands over. That is the promise."""
    app = _run(db_url, with_history=True)

    documents = [c.value for c in app.code]
    assert documents, "the document was not shown"
    assert "<spendifai_report" in documents[0]
    # A download button is its own element type, not a button.
    assert app.get("download_button"), "no way to save what is on screen"


def test_a_prompt_that_filled_the_context_is_said_out_loud(db_url):
    """4000 tokens into a 4096 context is the defect that cost hours."""
    app = _run(db_url, with_history=True)

    warnings = " ".join(w.value for w in app.warning)
    assert "4000" in warnings and "4096" in warnings, (
        f"context pressure was not surfaced; warnings were: {warnings!r}"
    )


def test_no_translation_key_reaches_the_screen_untranslated(db_url):
    """A missing key renders as its own name, which nobody reads as a label."""
    app = _run(db_url, with_history=True)

    on_screen = " ".join(
        [s.value for s in app.subheader]
        + [c.value for c in app.caption]
        + [w.value for w in app.warning]
    )
    assert "diagnostics." not in on_screen, f"untranslated key on screen: {on_screen!r}"


def test_what_is_drawn_carries_nothing_personal(db_url):
    """The no-personal-data rule is asserted on the document elsewhere.

    Here it is asserted on the page, because a value can reach the screen
    without reaching the file, and the screenshot of a support page travels
    just as far.
    """
    app = _run(db_url, with_history=True)

    drawn = " ".join(
        [s.value for s in app.subheader]
        + [c.value for c in app.caption]
        + [w.value for w in app.warning]
        + [c.value for c in app.code]
        + [str(t.value) for t in app.table]
    )
    for leak in LEAKS:
        assert leak not in drawn, f"the page draws {leak!r}"


def test_in_the_packaged_window_the_report_is_written_not_downloaded(db_url, tmp_path):
    """The defect reported on 2026-09-25, as the page now behaves.

    A download button in that window navigates to the document instead of
    saving it, so it must not be on the page at all.
    """
    app = _run(db_url, with_history=True, packaged=True, destination=str(tmp_path))

    assert not app.exception
    assert not app.get("download_button")

    save = [b for b in app.button if b.label == "Salva il documento"]
    assert save, "nothing offers to save the document"
    save[0].click().run(timeout=60)

    written = list(tmp_path.glob("spendifai-report-*.xml"))
    assert written, "the document was not written"
    assert "<spendifai_report" in written[0].read_text()


def test_the_packaged_window_offers_to_write_the_message_itself(db_url, tmp_path):
    """A mailto: link is as dead as a download in that window."""
    app = _run(db_url, with_history=True, packaged=True, destination=str(tmp_path))

    labels = [b.label for b in app.button]
    assert "Scrivi all'assistenza" in labels

    body = " ".join(m.value for m in app.markdown)
    assert "mailto:" not in body, "the desktop text still points at a dead link"
    assert "support@spendif.ai" in body
