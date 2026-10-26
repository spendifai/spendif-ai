"""Which model the first launch picks: VRAM rounded to the GB, one rule for
onboarding and download (spec 15, section 5)."""
from __future__ import annotations

import subprocess

import pytest

from core import model_manager as mm


def _fake_output(outputs: dict[str, str]):
    """subprocess.check_output answering per command, failing for the rest."""
    def run(cmd, *args, **kwargs):
        key = cmd[0] if cmd[0] != "rocm-smi" else " ".join(cmd)
        if key in outputs:
            return outputs[key]
        raise FileNotFoundError(cmd[0])
    return run


@pytest.mark.parametrize("mib, expected", [
    ("12227", 12),   # RTX 5070 Ti Laptop: a 12 GB card that never says 12288
    ("12288", 12),
    ("11264", 11),   # a real 11 GB card stays 11
    ("8188", 8),
    ("6144", 6),
])
def test_nvidia_vram_rounds_to_nearest_gb(monkeypatch, mib, expected):
    monkeypatch.setattr(subprocess, "check_output", _fake_output({"nvidia-smi": mib + "\n"}))
    assert mm._detect_vram_gb() == expected


def test_rocm_csv_vram_rounds_to_nearest_gb(monkeypatch):
    total = 12227 * 1024 * 1024
    monkeypatch.setattr(subprocess, "check_output", _fake_output({
        "rocm-smi --showmeminfo vram --csv": f"device,VRAM Total Memory (B),VRAM Total Used Memory (B)\n0,{total},0\n",
    }))
    assert mm._detect_vram_gb() == 12


def test_vulkan_heap_rounds_to_nearest_gb(monkeypatch):
    heap = 12227 * 1024 * 1024
    monkeypatch.setattr(subprocess, "check_output", _fake_output({
        "vulkaninfo": f"memoryHeaps[0]:\n  heapSize = {heap} (MEMORY_HEAP_DEVICE_LOCAL_BIT)\n",
    }))
    assert mm._detect_vram_gb() == 12


@pytest.mark.parametrize("hw, expected", [
    ({"os": "Windows", "ram_gb": 32, "vram_gb": 12}, 12),
    ({"os": "Windows", "ram_gb": 8, "vram_gb": 12}, 8),
    ({"os": "Linux", "ram_gb": 16, "vram_gb": 0}, 16),    # no card: RAM
    ({"os": "Darwin", "ram_gb": 32, "vram_gb": 32}, 32),  # unified memory
])
def test_effective_memory(hw, expected):
    assert mm.effective_memory_gb(hw) == expected


def test_twelve_gb_card_gets_the_7b():
    hw = {"os": "Windows", "ram_gb": 32, "vram_gb": 12}
    assert mm.recommended_chain(hw)[0].id == "qwen2.5-7b"


def test_download_uses_the_same_choice(monkeypatch, tmp_path):
    """ensure_model_available tries first what the onboarding announces."""
    hw = {"os": "Windows", "arch": "AMD64", "ram_gb": 32, "vram_gb": 12, "gpu": "x", "gpu_cores": 0}
    tried: list[str] = []

    def fake_download(repo, filename, dest, progress_callback=None):
        tried.append(filename)
        raise OSError("offline")

    monkeypatch.setattr(mm, "list_local_models", lambda: [])
    monkeypatch.setattr(mm, "detect_hw", lambda: hw)
    monkeypatch.setattr(mm, "_download_from_hf", fake_download)
    monkeypatch.setattr(mm, "MODELS_DIR", tmp_path)
    assert mm.ensure_model_available() is None
    assert tried[0] == mm.recommended_chain(hw)[0].filename


def test_onboarding_recommendation_follows_the_card():
    from services.llm_service import get_recommended_model
    hw = {"os": "Windows", "ram_gb": 32, "vram_gb": 12}
    assert get_recommended_model(hw).id == "qwen2.5-7b"
