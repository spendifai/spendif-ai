"""Model manager — HW detection + automatic model download (T-05).

Detects system hardware, recommends the best GGUF model from the registry,
and downloads it to ~/.spendifai/models/ if not already present.

Usage:
    from core.model_manager import detect_hw, ensure_model_available

    hw = detect_hw()
    # {'os': 'Darwin', 'arch': 'arm64', 'ram_gb': 32, 'gpu': 'Apple M1 Max', 'gpu_cores': 32}

    model_path = ensure_model_available(progress_callback=lambda pct, msg: print(f"{pct:.0%} {msg}"))
    # Downloads if needed, returns path to GGUF file
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Callable

from config import ModelInfo, get_fallback_chain, get_recommended_model


def _build_fallback_chain(effective_gb: int) -> list[ModelInfo]:
    """Local wrapper around ``config.get_fallback_chain`` so unit tests can
    monkey-patch it without touching the global registry loader."""
    return get_fallback_chain(effective_gb)


def effective_memory_gb(hw: dict[str, Any]) -> int:
    """The memory the model has to fit in (spec 15, section 5).

    With a discrete card VRAM is the bottleneck; on macOS (unified memory)
    vram_gb == ram_gb, so min() is a no-op; vram_gb == 0 means no card, and
    RAM is the constraint. Onboarding and download both read it from here,
    so the model announced is the model downloaded.
    """
    ram_gb, vram_gb = hw["ram_gb"], hw["vram_gb"]
    return min(ram_gb, vram_gb) if vram_gb > 0 else ram_gb


def recommended_chain(hw: dict[str, Any]) -> list[ModelInfo]:
    """The recommended model for this hardware, then its smaller fallbacks."""
    return _build_fallback_chain(effective_memory_gb(hw))


def _to_gb(value: float, unit: int) -> int:
    """Round to the nearest GB. Cards never report their full size: a 12 GB
    RTX 5070 Ti Laptop says 12227 MiB, which integer division made 11, below
    the 12 GB tier."""
    return int(round(value / unit))
from support.logging import setup_logging

logger = setup_logging()

MODELS_DIR = Path.home() / ".spendifai" / "models"


# ── HW Detection ─────────────────────────────────────────────────────────────


def detect_hw() -> dict[str, Any]:
    """Detect system hardware: OS, arch, RAM, GPU, VRAM.

    Returns a dict with keys: os, arch, ram_gb, vram_gb, gpu, gpu_cores.
    On macOS (unified memory) vram_gb equals ram_gb.
    On Linux/Windows vram_gb is detected via nvidia-smi or rocm-smi (0 if no GPU).
    """
    info: dict[str, Any] = {
        "os": platform.system(),         # Darwin, Linux, Windows
        "arch": platform.machine(),      # arm64, x86_64
        "ram_gb": _get_ram_gb(),
        "vram_gb": 0,
        "gpu": "unknown",
        "gpu_cores": 0,
    }

    if info["os"] == "Darwin":
        info["gpu"], info["gpu_cores"] = _detect_macos_gpu()
        # Apple Silicon: unified memory — VRAM == RAM
        info["vram_gb"] = info["ram_gb"]
    elif info["os"] in ("Linux", "Windows"):
        info["gpu"] = _detect_linux_gpu()
        info["vram_gb"] = _detect_vram_gb()

    logger.info(
        f"detect_hw: {info['os']} {info['arch']}, "
        f"RAM={info['ram_gb']} GB, VRAM={info['vram_gb']} GB, "
        f"GPU={info['gpu']} ({info['gpu_cores']} cores)"
    )
    return info


def _get_ram_gb() -> int:
    """Get total RAM in GB (rounded down)."""
    try:
        if platform.system() == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip()) // (1024 ** 3)
        elif platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // (1024 ** 2)
        else:
            # Windows
            import ctypes
            kernel32 = ctypes.windll.kernel32
            c_ulong = ctypes.c_ulonglong
            mem = c_ulong()
            kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(mem))
            return mem.value // (1024 ** 2)
    except Exception as exc:
        logger.warning(f"_get_ram_gb: failed — {exc}")
    return 8  # safe fallback


def _detect_macos_gpu() -> tuple[str, int]:
    """Detect Apple Silicon GPU on macOS."""
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
        # Extract GPU core count from system_profiler
        try:
            sp = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"], text=True, timeout=5
            )
            for line in sp.splitlines():
                if "Total Number of Cores" in line:
                    cores = int("".join(c for c in line.split(":")[-1] if c.isdigit()))
                    return out, cores
        except Exception:
            pass
        return out, 0
    except Exception:
        return "unknown", 0


def _detect_linux_gpu() -> str:
    """Detect GPU on Linux/Windows (NVIDIA, AMD ROCm, or CPU-only)."""
    # Try NVIDIA first
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True, timeout=5,
        ).strip()
        if out:
            return out.splitlines()[0]
    except Exception:
        pass
    # Try AMD ROCm
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showproductname"], text=True, timeout=5,
        ).strip()
        for line in out.splitlines():
            if "Card series" in line or "GPU" in line:
                return line.split(":")[-1].strip()
    except Exception:
        pass
    # Try Vulkan (AMD without ROCm, or other Vulkan-capable GPUs)
    try:
        out = subprocess.check_output(
            ["vulkaninfo", "--summary"], text=True, timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        for line in out.splitlines():
            if "deviceName" in line:
                return line.split("=")[-1].strip()
    except Exception:
        pass
    return "CPU only"


def _detect_vram_gb() -> int:
    """Detect GPU VRAM in GB via nvidia-smi or rocm-smi. Returns 0 if no GPU."""
    # NVIDIA
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        ).strip()
        if out:
            # nvidia-smi reports MiB; take first GPU
            mib = int(out.splitlines()[0].strip())
            return _to_gb(mib, 1024)
    except Exception:
        pass
    # AMD ROCm — try multiple parsing strategies (output format varies by version)
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--csv"], text=True, timeout=5,
        ).strip()
        for line in out.splitlines():
            parts = line.split(",")
            if len(parts) >= 2 and parts[0].strip().isdigit():
                # rocm-smi --csv: GPU#, VRAM Total (B), VRAM Used (B)
                total_bytes = int(parts[1].strip())
                return _to_gb(total_bytes, 1024 ** 3)
    except Exception:
        pass
    # Fallback: non-CSV output (some ROCm versions)
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram"], text=True, timeout=5,
        ).strip()
        for line in out.splitlines():
            low = line.lower()
            if "total" in low:
                # Extract first number — could be bytes or MiB depending on version
                import re
                nums = re.findall(r"(\d+)", line)
                if nums:
                    val = int(nums[0])
                    # Heuristic: if > 1_000_000 it's bytes, otherwise MiB
                    if val > 1_000_000:
                        return _to_gb(val, 1024 ** 3)
                    else:
                        return _to_gb(val, 1024)
    except Exception:
        pass
    # Vulkan fallback (AMD without ROCm, or other Vulkan GPUs)
    try:
        out = subprocess.check_output(
            ["vulkaninfo"], text=True, timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        import re
        for line in out.splitlines():
            if "DEVICE_LOCAL" in line and "heapSize" in line:
                m = re.search(r"heapSize\s*=\s*(\d+)", line)
                if m:
                    heap_bytes = int(m.group(1))
                    if heap_bytes > 1_000_000_000:  # sanity check: > 1 GB
                        return _to_gb(heap_bytes, 1024 ** 3)
    except Exception:
        pass
    return 0


# ── Model Download ───────────────────────────────────────────────────────────

ProgressCallback = Callable[[float, str], None]  # (pct 0-1, message)


def list_local_models() -> list[Path]:
    """List all GGUF files in ~/.spendifai/models/."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(MODELS_DIR.glob("*.gguf"))


def ensure_model_available(
    progress_callback: ProgressCallback | None = None,
) -> str | None:
    """Ensure at least one GGUF model is available. Download if needed.

    Returns the path to the best available model, or None if download failed.
    """
    def _progress(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    # 1. Check existing models
    existing = list_local_models()
    if existing:
        logger.info(f"ensure_model_available: found {len(existing)} models, using {existing[0].name}")
        _progress(1.0, f"Modello disponibile: {existing[0].name}")
        return str(existing[0])

    # 2. Detect HW and pick model
    _progress(0.05, "Rilevamento hardware...")
    hw = detect_hw()
    ram_gb = hw["ram_gb"]
    vram_gb = hw["vram_gb"]

    effective_gb = effective_memory_gb(hw)

    # Try the recommended model first; on failure (404, 401, network, …)
    # fall back to the next-smaller tier and retry, down to the smallest
    # model in the registry. This makes the bundle resilient against a
    # single HF repo going gated/private (AI-59 was exactly this: the
    # 16 GB tier pointed at the gated google/gemma-3-12b-it-GGUF and the
    # entire AI feature was disabled until we shipped a registry fix).
    fallback_chain = _build_fallback_chain(effective_gb)
    if not fallback_chain:
        logger.error(
            "ensure_model_available: no model found in registry for "
            "effective=%d GB (RAM=%d, VRAM=%d)", effective_gb, ram_gb, vram_gb,
        )
        _progress(1.0, "Nessun modello compatibile trovato")
        return None

    last_exc: Exception | None = None
    for attempt_idx, candidate in enumerate(fallback_chain):
        if attempt_idx == 0:
            _progress(0.10, f"Consigliato: {candidate.name} ({candidate.size_mb} MB)")
            logger.info(
                f"ensure_model_available: RAM={ram_gb} GB, VRAM={vram_gb} GB, "
                f"effective={effective_gb} GB → {candidate.id} "
                f"({candidate.size_mb} MB, tier={candidate.tier})"
            )
        else:
            logger.warning(
                "ensure_model_available: falling back to %s (%s MB, tier=%s) "
                "after previous candidate failed", candidate.id, candidate.size_mb, candidate.tier,
            )
            _progress(0.10, f"Ripiego: {candidate.name} ({candidate.size_mb} MB)")

        dest_path = MODELS_DIR / candidate.filename
        try:
            _progress(0.15, f"Download {candidate.name}...")
            _download_from_hf(
                repo=candidate.repo,
                filename=candidate.filename,
                dest=dest_path,
                progress_callback=lambda pct: _progress(0.15 + pct * 0.80, f"Download: {pct:.0%}"),
            )
            _progress(0.95, "Download completato, verifica...")

            if dest_path.exists() and dest_path.stat().st_size > 100_000:
                logger.info(f"ensure_model_available: downloaded {dest_path} ({dest_path.stat().st_size:,} bytes)")
                _progress(1.0, f"Pronto: {candidate.name}")
                return str(dest_path)
            logger.error(f"ensure_model_available: download incomplete — {dest_path}, trying fallback")
        except Exception as exc:
            last_exc = exc
            logger.error(f"ensure_model_available: download failed for {candidate.id} — {exc}")
            # try the next fallback

    # Exhausted every fallback
    logger.error("ensure_model_available: all fallbacks exhausted, last error=%s", last_exc)
    _progress(1.0, f"Download fallito anche con tutti i fallback: {last_exc}")
    return None


def _make_callback_tqdm(tqdm_base, progress_callback):
    """Return a tqdm subclass whose ``update()`` propagates progress to a callback.

    Extracted from ``_download_from_hf`` so it can be unit-tested without
    needing a real HuggingFace download.
    """
    if tqdm_base is None or progress_callback is None:
        return tqdm_base

    class _CallbackTqdm(tqdm_base):  # type: ignore[misc, valid-type]
        def update(self, n=1):
            ret = super().update(n)
            try:
                if self.total:
                    progress_callback(min(1.0, self.n / self.total))
            except Exception:
                # Never let a UI hiccup kill the download.
                pass
            return ret

    return _CallbackTqdm


def _download_from_hf(
    repo: str,
    filename: str,
    dest: Path,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Download a single file from HuggingFace Hub.

    `hf_hub_download` uses an internal `tqdm` progress bar to report download
    progress to stdout. To surface that progress to our caller (the desktop
    launcher's status file consumer), we hand `hf_hub_download` a custom
    `tqdm` subclass whose `update()` fires the caller's callback after every
    chunk. Without this hook the caller would only see a single `1.0` at the
    very end — useless for any UX that wants live ETA.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download
        try:
            from tqdm.auto import tqdm as _tqdm_base
        except ImportError:
            _tqdm_base = None

        tqdm_cls = _make_callback_tqdm(_tqdm_base, progress_callback)

        # hf_hub_download handles caching, resume, etc.
        downloaded = hf_hub_download(
            repo_id=repo,
            filename=filename,
            local_dir=str(MODELS_DIR),
            local_dir_use_symlinks=False,
            tqdm_class=tqdm_cls,
        )
        # Move to expected path if needed
        dl_path = Path(downloaded)
        if dl_path != dest and dl_path.exists():
            dl_path.rename(dest)

        if progress_callback:
            progress_callback(1.0)

    except ImportError:
        # Fallback: download with requests
        logger.warning("huggingface_hub not installed, using requests fallback")
        _download_with_requests(
            f"https://huggingface.co/{repo}/resolve/main/{filename}",
            dest,
            progress_callback,
        )


def _download_with_requests(
    url: str,
    dest: Path,
    progress_callback: Callable[[float], None] | None = None,
) -> None:
    """Fallback download with requests + progress."""
    import requests

    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))
    downloaded = 0

    with open(dest, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
            f.write(chunk)
            downloaded += len(chunk)
            if progress_callback and total > 0:
                progress_callback(downloaded / total)
