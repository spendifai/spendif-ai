"""What is actually running: interpreter, inference library, active backends.

WHY THIS EXISTS, SEPARATE FROM detect_hw()
    `core.model_manager.detect_hw()` answers what the machine HAS: processor,
    memory, graphics card. This module answers what the application IS USING,
    which is a different question and the one that goes unanswered when a user
    reports that inference is slow or broken.

    The distinction is not academic. A machine with a 16 GB Radeon reports that
    card in detect_hw() while the model runs entirely on the processor,
    because acceleration is a compile-time choice in the inference library and
    not a property of the hardware. Reading only the hardware there suggests a
    GPU is in use when none is.

WHY THE BACKEND LIST IS READ FROM THE LIBRARY AND NOT ASSUMED
    ggml registers one backend per accelerator it could load, and the list is
    the ground truth: it says whether the build that shipped can talk to the
    graphics card at all. Nothing else does. It is also cheap - no model is
    loaded to ask.

NO PERSONAL DATA
    Everything here is a version, a name or a flag. Paths are deliberately
    absent: an interpreter path or a model path carries the account name of
    whoever is running the application. Model files are reported by file name
    only, which names the model and nobody else.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Any

logger = logging.getLogger("SPENDIFY")


def _is_frozen() -> bool:
    """True when running from a packaged build rather than from source."""
    return bool(getattr(sys, "frozen", False))


def _llama_cpp_version() -> str:
    try:
        import llama_cpp

        return str(getattr(llama_cpp, "__version__", "unknown"))
    except Exception:  # noqa: BLE001 - a diagnostics page must never raise
        return "not installed"


def _ggml_devices() -> list[str]:
    """Names of the accelerators the inference library has registered.

    Returns e.g. ["MTL0", "CPU"] or ["CPU"]. An empty list means the library
    is present but registered nothing, which is itself the answer when a user
    reports that no model will load.

    Importing llama_cpp is enough to register the backends that were linked at
    build time. Builds made with dynamic backends register nothing until
    somebody loads them, and the loader is called by the code that creates the
    model; asking here before that has happened would under-report, so the
    caller is expected to ask after a model has been built at least once, or to
    accept the link-time list.
    """
    try:
        import ctypes

        import llama_cpp.llama_cpp as C

        count = C._lib.ggml_backend_dev_count
        count.restype = ctypes.c_size_t
        get = C._lib.ggml_backend_dev_get
        get.argtypes = [ctypes.c_size_t]
        get.restype = ctypes.c_void_p
        name = C._lib.ggml_backend_dev_name
        name.argtypes = [ctypes.c_void_p]
        name.restype = ctypes.c_char_p

        return [name(get(i)).decode(errors="replace") for i in range(count())]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_info: cannot list ggml devices (%s)", exc)
        return []


def accelerator_available() -> bool:
    """Whether the inference library can put work on something other than the CPU.

    Asked of the library, not of the machine. Having a graphics card and using
    it are different facts, and the second one is a property of how the
    inference library was built: a card is present on plenty of machines where
    the build cannot address it, and setting layers on a card the build cannot
    reach is how a model fails to load at all.

    The caveat from _ggml_devices applies: a build with dynamic backends
    registers nothing until a model has been loaded once, so this can answer
    "no" too early. Answering "no" wrongly costs speed; answering "yes"
    wrongly costs the application failing to start a model, which is the
    failure this project has already shipped twice.
    """
    return any(d.upper() != "CPU" for d in _ggml_devices())


def _emulated() -> bool:
    try:
        from core.platform_info import is_emulated_x64_on_arm

        return bool(is_emulated_x64_on_arm())
    except Exception:  # noqa: BLE001
        return False


def collect() -> dict[str, Any]:
    """Runtime facts, safe to show and to export.

    Never raises: every field falls back to a string that says so, because a
    diagnostics report that crashes tells its reader nothing at all.
    """
    devices = _ggml_devices()
    return {
        "os_version": platform.platform(),          # names the OS and its exact release
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        # Distinguishes a packaged build, which carries its own interpreter,
        # from a source install running on the system one. It decides whether
        # the interpreter version above is ours or the distribution's.
        "packaged_build": _is_frozen(),
        "emulated_x64_on_arm": _emulated(),
        "llama_cpp_version": _llama_cpp_version(),
        "inference_devices": devices,
        # The question a reader actually has: is anything but the processor in
        # play. Anything other than CPU is an accelerator.
        "gpu_acceleration_active": any(d.upper() != "CPU" for d in devices),
    }
