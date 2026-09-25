"""Running without a native window, which on Linux is the only way.

WHY IT EXISTS
    The launcher treated a failed import of the window library as fatal and
    re-raised. That is correct on macOS and Windows, where the window is there;
    on Linux it is the end of the application, because there is no window to
    import.

    PyGObject publishes no wheels and pycairo publishes them for Windows only,
    so a bundled Python cannot use the distribution's python3-gi: that package
    is compiled for the ABI of the distribution's own Python, which is the
    exact dependency the self-contained bundle exists to remove. Compiling
    PyGObject into the build image and hoping the GLib on the target machine
    agrees is the other option. Opening the browser works everywhere by
    construction.

WHAT IS ASSERTED HERE
    That the import failing is not the end, and that the browser path starts
    the server, waits for it to answer, opens the address, and only then
    blocks. Not the window path, which is unchanged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "desktop" / "launcher.py"


def _source() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_a_missing_window_library_is_not_fatal():
    """The line that used to end the application on Linux."""
    source = _source()

    assert "HAVE_NATIVE_WINDOW" in source, "the two ways of running are not distinguished"
    # The import guard must not re-raise: the old one ended with `raise`
    # immediately after printing FATAL.
    guard = source[source.index("import webview"): source.index("# Paths")]
    assert "FATAL" not in guard, "a missing window is still reported as fatal"
    assert "\n    raise" not in guard, "the import still re-raises"


def test_the_browser_path_exists_and_is_chosen_when_there_is_no_window():
    source = _source()

    assert "def _run_in_browser(" in source
    branch = source.index("if not HAVE_NATIVE_WINDOW:")
    call = source.index("_run_in_browser(app_dir, port)")
    window = source.index("webview.create_window(")
    assert branch < call < window, (
        "the window is created before the browser path is considered"
    )


def test_the_browser_opens_only_after_the_server_answers():
    """Opening the address first shows the person a connection error.

    The window path has a splash to look at while the server starts. A browser
    tab has nothing, so the order is the whole user experience here.
    """
    source = _source()
    body = source[source.index("def _run_in_browser("): source.index("\ndef main()")]

    wait_at = body.index("_wait_for_port(port)")
    open_at = body.index("webbrowser.open(url)")
    assert wait_at < open_at, "the browser is opened before the server answers"


def test_the_server_is_tracked_so_the_cleanup_can_reach_it():
    """A server started outside the tracked list is a server nothing stops."""
    source = _source()
    body = source[source.index("def _run_in_browser("): source.index("\ndef main()")]

    assert "_start_streamlit(port, app_dir)" in body
    assert "_write_instance_lock(proc)" in body, (
        "the instance lock is not written, so the next launch cannot find this one"
    )
    assert "proc.wait()" in body, (
        "nothing blocks on the server, so the launcher would exit immediately "
        "and take the interface with it"
    )


def test_a_browser_that_will_not_open_does_not_stop_the_server():
    """The address is on screen and in the log; somebody can paste it."""
    source = _source()
    body = source[source.index("def _run_in_browser("): source.index("\ndef main()")]

    open_at = body.index("webbrowser.open(url)")
    tail = body[open_at:]
    assert "except Exception" in tail, "a browser that refuses to open is fatal"
    assert "proc.wait()" in tail, "the server is abandoned when the browser fails"


def test_it_really_imports_with_no_window_library(tmp_path):
    """The claim above, run rather than read.

    In a separate process, because importing the launcher redirects its own
    output and rotates a log file, and because the window library has to be
    made unavailable before the import rather than after. HOME is moved aside
    so the real log is left alone.
    """
    import subprocess
    import textwrap

    probe = textwrap.dedent(
        """
        import os, sys

        # The import itself must fail, which is the situation on a Linux
        # machine with no GTK. None in sys.modules is what makes an import
        # raise; a finder written against find_module would do nothing at all,
        # since that API was removed in 3.12.
        sys.modules["webview"] = None

        sys.path.insert(0, ".")
        import desktop.launcher as L

        # The launcher redirects stdout and stderr into its own log at import
        # time, which is the whole point of that code, so the answer goes to a
        # file instead.
        with open(os.environ["PROBE_RESULT"], "w") as fh:
            fh.write(f"native={L.HAVE_NATIVE_WINDOW} browser={callable(L._run_in_browser)}")
        """
    )
    result_file = tmp_path / "answer.txt"
    env = {
        "HOME": str(tmp_path),
        "PATH": __import__("os").environ.get("PATH", ""),
        "SPENDIFAI_LOG_DIR": str(tmp_path / "logs"),
        "PROBE_RESULT": str(result_file),
    }
    outcome = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(LAUNCHER.parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if not result_file.exists():
        log = tmp_path / "Library" / "Logs" / "spendifai-launcher.log"
        detail = log.read_text(encoding="utf-8")[-2000:] if log.exists() else outcome.stderr
        pytest.fail(f"the launcher did not import without a window library:\n{detail}")

    assert result_file.read_text(encoding="utf-8") == "native=False browser=True"
