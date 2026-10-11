import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def _resolve_log_dir() -> Path:
    """Pick a writable directory for the app log file.

    Order of preference:
    1. ``$SPENDIFAI_LOG_DIR`` if set (caller override).
    2. ``~/.spendifai/logs/`` — always writable per-user, survives reinstalls.
    3. ``./logs/`` — dev mode, only when running from a writable source tree
       (i.e. not from a PyInstaller bundle in a read-only Applications dir).

    The PyInstaller-frozen desktop bundle lives in ``/Applications/.../Frameworks``
    which is read-only — option 3 would crash there.
    """
    override = os.environ.get("SPENDIFAI_LOG_DIR")
    if override:
        return Path(override).expanduser()

    # Frozen → always use the per-user dotdir.
    if getattr(sys, "frozen", False):
        return Path.home() / ".spendifai" / "logs"

    # Source mode → keep the historical `./logs` if the cwd is writable.
    try:
        Path("logs").mkdir(exist_ok=True)
        # touch to confirm write access
        probe = Path("logs") / ".write_probe"
        probe.touch()
        probe.unlink()
        return Path("logs")
    except (OSError, PermissionError):
        # Fall through to the per-user dotdir.
        pass

    return Path.home() / ".spendifai" / "logs"


def setup_logging():
    # Streamlit re-imports every module on each rerun and every module that
    # writes logs does `logger = setup_logging()` at import time, so this
    # function is called dozens of times per session. Two problems if we
    # don't guard:
    #   1. `logging.basicConfig(...)` is a no-op once the root logger has
    #      handlers — so after the first call, our reconfiguration silently
    #      does nothing.
    #   2. We still compute `log_file = ... datetime.now() ...` and open a
    #      brand-new FileHandler on disk, which gets dropped on the floor
    #      because basicConfig didn't attach it. Result: hundreds of empty
    #      `app_<timestamp>.log` files in the log dir, only the very first
    #      one (whose handler actually got attached) captures anything.
    # Guard: if the root logger is already configured, just return the
    # named logger without touching the filesystem.
    spendify_logger = logging.getLogger("SPENDIFY")
    if logging.getLogger().handlers:
        return spendify_logger

    log_dir = _resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        handlers=[
            logging.FileHandler(str(log_file)),
            logging.StreamHandler(),
        ],
    )

    return spendify_logger


# Installed once per process. Streamlit re-imports every module on each rerun,
# so this module-level flag is what keeps the hooks from stacking.
_HOOKS_INSTALLED = False


def capture_unhandled_exceptions():
    """Send what nobody caught to the application log as well as to stderr.

    An exception raised outside a page render - in a worker thread, at import
    time, during shutdown - reaches stderr and nothing else. In the windowed
    desktop bundle stderr belongs to the launcher, whose log is rewritten at
    every start, so the trace of a crash disappears the moment the person does
    the obvious thing and restarts the application. The log that survives, and
    the one the technical report for support collects, is this one.

    The previous hooks are chained rather than replaced: whatever the runtime
    was doing with an unhandled exception it keeps doing.
    """
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return

    import threading

    logger = logging.getLogger("SPENDIFY")
    previous_excepthook = sys.excepthook
    previous_threadhook = threading.excepthook

    def _log_and_defer(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt is a person pressing Ctrl-C, not a defect.
        if not issubclass(exc_type, KeyboardInterrupt):
            logger.critical(
                "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
            )
        previous_excepthook(exc_type, exc_value, exc_tb)

    def _log_and_defer_thread(args):
        if args.exc_type is not SystemExit:
            logger.critical(
                "Unhandled exception in thread %s",
                args.thread.name if args.thread else "unknown",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        previous_threadhook(args)

    sys.excepthook = _log_and_defer
    threading.excepthook = _log_and_defer_thread
    _HOOKS_INSTALLED = True
