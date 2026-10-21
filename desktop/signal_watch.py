"""Act on SIGTERM / SIGINT even while the main thread is in native code.

A Python signal handler runs only when the main thread is back in the
interpreter. The desktop launcher's main thread sits in the pywebview native
window loop, so a SIGTERM was never handled: on 2026-10-20 the launcher was
still alive 8 s after one, and the Streamlit server it started outlived it.
The C-level handler still writes the signal number to the wakeup fd; a
thread blocked on that fd runs the cleanup and ends the process.
"""
from __future__ import annotations

import os
import signal
import threading


def install(cleanup, signals=(signal.SIGTERM, signal.SIGINT), exit_fn=os._exit) -> None:
    """From the main thread: on any of *signals*, run *cleanup* then exit.

    Python handlers must already be installed for *signals* (the wakeup fd
    is written only for signals that have one).
    """
    r, w = os.pipe()
    os.set_blocking(w, False)
    signal.set_wakeup_fd(w)
    wanted = {int(s) for s in signals}

    def _watch() -> None:
        while True:
            try:
                data = os.read(r, 1)
            except OSError:
                return
            if data and data[0] in wanted:
                print(f"signal {data[0]} received: cleaning up", flush=True)
                try:
                    cleanup()
                finally:
                    exit_fn(0)
                return

    threading.Thread(target=_watch, name="signal-watch", daemon=True).start()
