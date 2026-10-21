"""The Streamlit server never outlives the desktop launcher that started it.

The launcher starts Streamlit in its own process group, so a launcher that
dies without its cleanup (kill -9, a crash, a forced quit from Activity
Monitor) left the server running: the model loaded on the GPU, an import
still writing to the ledger, the port held. On 2026-10-20 nine of them were
found alive at once.

The launcher passes its PID in SPENDIFAI_LAUNCHER_PID; a daemon thread here
checks it every few seconds and ends this process as soon as the launcher is
gone. ``os._exit`` on purpose: a polite exit would wait for the import
thread, which is exactly what must not go on writing.
"""
from __future__ import annotations

import os
import threading
import time

ENV_VAR = "SPENDIFAI_LAUNCHER_PID"
_INTERVAL_S = 2.0
_started = False
_lock = threading.Lock()


def launcher_alive(pid: int) -> bool:
    """True while the launcher with *pid* is alive and still our parent.

    On Unix an orphan is adopted by launchd/init, so a parent that is no
    longer *pid* means the launcher is gone even if the PID were reused.
    """
    if pid <= 1:
        return False
    if os.name == "posix" and os.getppid() != pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Windows: os.kill(pid, 0) is not a probe there; trust the parent check
        # being unavailable and keep running rather than exit by mistake.
        return True
    return True


def _watch(pid: int, interval: float, exit_fn) -> None:
    while True:
        time.sleep(interval)
        if not launcher_alive(pid):
            exit_fn(0)
            return


def start(interval: float = _INTERVAL_S, exit_fn=os._exit) -> bool:
    """Start the watchdog once per process, if a launcher PID was given.

    Returns True when a watchdog is running (started now or before). Run
    from the source tree with ``streamlit run`` there is no launcher and
    nothing is watched.
    """
    global _started
    raw = os.environ.get(ENV_VAR, "")
    if not raw.isdigit():
        return False
    with _lock:
        if not _started:
            threading.Thread(
                target=_watch, args=(int(raw), interval, exit_fn),
                name="launcher-watchdog", daemon=True,
            ).start()
            _started = True
    return True
