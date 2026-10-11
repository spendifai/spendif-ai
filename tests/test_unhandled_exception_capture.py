"""An exception nobody caught must reach the application log.

On 2026-09-23 the traceback of a page that crashed on every installed build
existed in exactly one file: the launcher log, which was rewritten at every
start. Restarting after a crash, which is what anybody does, destroyed the only
copy. The technical report for support collects the application log, so it
arrived empty on precisely the defect it was built to describe.
"""

from __future__ import annotations

import logging
import sys
import threading

import pytest

from support import logging as app_logging


@pytest.fixture()
def hooks_restored():
    """The hooks are process-wide; put them back whatever the test does."""
    saved = (sys.excepthook, threading.excepthook, app_logging._HOOKS_INSTALLED)
    app_logging._HOOKS_INSTALLED = False
    yield
    sys.excepthook, threading.excepthook, app_logging._HOOKS_INSTALLED = saved


def test_unhandled_exception_is_logged_and_still_reaches_the_previous_hook(hooks_restored, caplog):
    seen = []
    sys.excepthook = lambda *args: seen.append(args)

    app_logging.capture_unhandled_exceptions()

    try:
        raise ValueError("the page exploded")
    except ValueError:
        with caplog.at_level(logging.CRITICAL, logger="SPENDIFY"):
            sys.excepthook(*sys.exc_info())

    assert "the page exploded" in caplog.text
    assert "Unhandled exception" in caplog.text
    # Chained, not replaced: whatever the runtime did with the exception, it
    # keeps doing.
    assert len(seen) == 1


def test_keyboard_interrupt_is_not_reported_as_a_defect(hooks_restored, caplog):
    sys.excepthook = lambda *args: None
    app_logging.capture_unhandled_exceptions()

    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        with caplog.at_level(logging.CRITICAL, logger="SPENDIFY"):
            sys.excepthook(*sys.exc_info())

    assert caplog.text == ""


def test_installing_twice_does_not_stack_hooks(hooks_restored):
    app_logging.capture_unhandled_exceptions()
    first = sys.excepthook
    app_logging.capture_unhandled_exceptions()

    # Streamlit re-imports every module on every rerun. Without the guard the
    # hooks nest once per rerun and a single exception is logged dozens of
    # times.
    assert sys.excepthook is first


# The warning comes from pytest's own thread-exception plugin, which is the
# hook we chain to. Seeing it here is the chaining working, not a problem.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_exceptions_are_logged_too(hooks_restored, caplog):
    app_logging.capture_unhandled_exceptions()

    def explode():
        raise RuntimeError("background work failed")

    with caplog.at_level(logging.CRITICAL, logger="SPENDIFY"):
        worker = threading.Thread(target=explode, name="importer")
        worker.start()
        worker.join()

    assert "background work failed" in caplog.text
    assert "importer" in caplog.text
