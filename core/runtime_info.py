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


def _ggml_libraries() -> list[Any]:
    """The loaded libraries that may export a ggml function, most likely first.

    WHY MORE THAN ONE
        On Linux and macOS asking any of them is enough: the dynamic linker
        resolves a symbol through the dependencies of the library it is asked
        of, so libllama answers for ggml as well. Windows does not do that.
        GetProcAddress sees only what the one DLL exports, and the backend
        functions live in ggml.dll (the registry and the loader) and
        ggml-base.dll (the devices), not in llama.dll.

        Asking llama.dll alone is how the Windows build loaded no plugin, not
        even the processor one, and how its Diagnostics page reported no device
        at all. Found on 2026-10-10 by the first Windows wheel the CI built:
        the wheel was complete and the probe could not find the loader.

    Never raises; a library that cannot be loaded is left out.
    """
    libs: list[Any] = []
    try:
        from llama_cpp._ggml import libggml  # ggml.dll / libggml.so

        libs.append(libggml)
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes
        from pathlib import Path

        import llama_cpp.llama_cpp as C

        lib_dir = Path(C.__file__).parent / "lib"
        for base in ("ggml-base.dll", "libggml-base.so", "libggml-base.dylib"):
            if (lib_dir / base).is_file():
                libs.append(ctypes.CDLL(str(lib_dir / base)))
                break
        libs.append(C._lib)
    except Exception:  # noqa: BLE001
        pass
    return libs


def ggml_function(name: str) -> Any | None:
    """The ggml function called `name`, from whichever library exports it.

    None when no loaded library does, which callers treat as a library too old
    to have it.
    """
    for lib in _ggml_libraries():
        try:
            return getattr(lib, name)
        except AttributeError:
            continue
    return None


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

        count = ggml_function("ggml_backend_dev_count")
        get = ggml_function("ggml_backend_dev_get")
        name = ggml_function("ggml_backend_dev_name")
        if count is None or get is None or name is None:
            return []
        count.restype = ctypes.c_size_t
        get.argtypes = [ctypes.c_size_t]
        get.restype = ctypes.c_void_p
        name.argtypes = [ctypes.c_void_p]
        name.restype = ctypes.c_char_p

        return [name(get(i)).decode(errors="replace") for i in range(count())]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_info: cannot list ggml devices (%s)", exc)
        return []


def _emulated() -> bool:
    try:
        from core.platform_info import is_emulated_x64_on_arm

        return bool(is_emulated_x64_on_arm())
    except Exception:  # noqa: BLE001
        return False


def _os_identity() -> tuple[str, str]:
    """The operating system as a person names it, and as a short identifier.

    platform.platform() names the kernel on Linux ("Linux-6.8.0-...-with-
    glibc2.39"), which says nothing about whether the machine runs Debian 13
    or Fedora 42, and that is the first thing a compatibility table needs.
    The distribution describes itself in /etc/os-release; reading it is the
    standard way, and it carries no personal data.

    Returns e.g. ("Debian GNU/Linux 13 (trixie)", "debian-13"),
    ("macOS 15.6", "macos-15.6"), ("Windows 11", "windows-11").
    """
    system = platform.system()
    try:
        if system == "Linux":
            info = platform.freedesktop_os_release()
            pretty = info.get("PRETTY_NAME") or info.get("NAME") or "Linux"
            ident = "-".join(p for p in (info.get("ID", "linux"), info.get("VERSION_ID", "")) if p)
            return pretty, ident
        if system == "Darwin":
            version = platform.mac_ver()[0]
            return f"macOS {version}".strip(), f"macos-{version}" if version else "macos"
        if system == "Windows":
            release = platform.release()
            return f"Windows {release}".strip(), f"windows-{release}" if release else "windows"
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_info: cannot name the operating system (%s)", exc)
    return system or "unknown", (system or "unknown").lower()


def collect() -> dict[str, Any]:
    """Runtime facts, safe to show and to export.

    Never raises: every field falls back to a string that says so, because a
    diagnostics report that crashes tells its reader nothing at all.
    """
    devices = _ggml_devices()
    os_name, os_id = _os_identity()
    return {
        "os_version": platform.platform(),          # names the OS and its exact release
        "os_name": os_name,                         # "Debian GNU/Linux 13 (trixie)"
        "os_id": os_id,                             # "debian-13"
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
