"""The Streamlit server never outlives the desktop launcher.

2026-10-20: nine Streamlit servers were alive at once, one still running an
import on the GPU. Two causes: a SIGTERM to the launcher was never handled
(its main thread sits in the native window loop, where Python handlers do
not run), and a launcher killed with -9 left its server running until the
next start. Both are tested here with real processes.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

ROOT = Path(__file__).resolve().parent.parent


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_the_server_ends_when_its_launcher_is_killed(tmp_path):
    """kill -9 on the launcher: no cleanup runs, the server ends by itself."""
    child_code = (
        "import time, sys; sys.path.insert(0, %r)\n"
        "from support.parent_watchdog import start\n"
        "assert start(interval=0.2)\n"
        "time.sleep(60)\n" % str(ROOT)
    )
    parent_code = (
        "import os, subprocess, sys, time\n"
        "env = dict(os.environ, SPENDIFAI_LAUNCHER_PID=str(os.getpid()))\n"
        "p = subprocess.Popen([sys.executable, '-c', %r], env=env, start_new_session=True)\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n" % child_code
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline())
    try:
        time.sleep(0.5)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)
        assert _gone_within(child_pid, 5), "the server outlived its launcher"
    finally:
        for pid in (child_pid,):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_without_a_launcher_nothing_is_watched(monkeypatch):
    from support import parent_watchdog
    monkeypatch.delenv(parent_watchdog.ENV_VAR, raising=False)
    assert parent_watchdog.start() is False


def test_sigterm_cleans_up_even_when_the_python_handler_cannot_exit(tmp_path):
    """The launcher's handler never gets to run in the native loop: simulated
    with a handler that does nothing. The signal thread still cleans up."""
    marker = tmp_path / "cleaned"
    code = (
        "import signal, sys, time; sys.path.insert(0, %r)\n"
        "signal.signal(signal.SIGTERM, lambda *a: None)\n"
        "from desktop.signal_watch import install\n"
        "install(lambda: open(%r, 'w').write('ok'))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n" % (str(ROOT), str(marker))
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        assert marker.read_text() == "ok"
    finally:
        if proc.poll() is None:
            proc.kill()
