"""The model in use is the recommended one, or the person knows it is not (spec 36).

Every test works in a temporary models folder: never the real ~/.spendifai.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from core import model_manager as mm
from services import llm_service

DEEPSEEK = "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf"
QWEN_7B = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
GEMMA_12B = "google_gemma-3-12b-it-Q4_K_M.gguf"
MAC_16 = {"os": "Darwin", "arch": "arm64", "ram_gb": 16, "vram_gb": 16, "gpu": "Apple M4", "gpu_cores": 10}
CARD_12 = {"os": "Windows", "arch": "AMD64", "ram_gb": 32, "vram_gb": 12, "gpu": "x", "gpu_cores": 0}


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    """A temporary models folder, and the hardware of an M4 with 16 GB.

    Points MODELS_DIR, detect_hw and the cached hardware at test values so no
    test reads the real folder or probes the real machine.
    """
    folder = tmp_path / "models"
    folder.mkdir()
    monkeypatch.setattr(mm, "MODELS_DIR", folder)
    monkeypatch.setattr(mm, "detect_hw", lambda: MAC_16)
    monkeypatch.setattr(mm, "_cached_hw", MAC_16)
    monkeypatch.delenv("LLAMA_CPP_MODEL_PATH", raising=False)
    return folder


def _put(folder: Path, name: str, size_bytes: int = 1000) -> Path:
    """Write a fake model file of the given size.

    Args:
        folder: the models folder.
        name: the file name.
        size_bytes: the file size, which decides "the smallest" (spec 36, 3).
    """
    path = folder / name
    path.write_bytes(b"0" * size_bytes)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Automatic choice (spec 36, 3)
# ─────────────────────────────────────────────────────────────────────────────

def test_recommended_wins_over_alphabetical_order(models_dir):
    for name in (DEEPSEEK, QWEN_7B, GEMMA_12B):
        _put(models_dir, name)
    assert mm.resolve_local_model().name == GEMMA_12B


def test_twelve_gb_card_takes_the_7b_even_with_gemma_on_disk(models_dir):
    for name in (DEEPSEEK, QWEN_7B, GEMMA_12B):
        _put(models_dir, name)
    assert mm.resolve_local_model(CARD_12).name == QWEN_7B


def test_untested_files_only_take_the_smallest(models_dir):
    _put(models_dir, DEEPSEEK, size_bytes=5000)
    _put(models_dir, "zz-other-model.gguf", size_bytes=2000)
    assert mm.resolve_local_model().name == "zz-other-model.gguf"


def test_renaming_an_untested_file_to_sort_first_changes_nothing(models_dir):
    _put(models_dir, "AAA-first-in-the-alphabet.gguf")
    _put(models_dir, GEMMA_12B)
    assert mm.resolve_local_model().name == GEMMA_12B


def test_empty_folder_resolves_to_none(models_dir):
    assert mm.resolve_local_model() is None


def test_backend_default_path_is_the_automatic_choice(models_dir):
    from core.llm_backends import LlamaCppBackend
    _put(models_dir, DEEPSEEK)
    gemma = _put(models_dir, GEMMA_12B)
    assert LlamaCppBackend._default_model_path() == str(gemma)


# ─────────────────────────────────────────────────────────────────────────────
# First launch and onboarding (spec 36, 3.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_first_launch_downloads_the_recommended_despite_an_untested_file(models_dir, monkeypatch):
    _put(models_dir, DEEPSEEK)
    tried: list[str] = []

    def fake_download(repo, filename, dest, progress_callback=None):
        tried.append(filename)
        dest.write_bytes(b"0" * 200_000)

    monkeypatch.setattr(mm, "_download_from_hf", fake_download)
    assert Path(mm.ensure_model_available()).name == GEMMA_12B
    assert tried == [GEMMA_12B]


def test_after_onboarding_nothing_is_downloaded(models_dir, monkeypatch):
    _put(models_dir, DEEPSEEK)
    monkeypatch.setattr(mm, "_download_from_hf", lambda *a, **k: pytest.fail("no download after onboarding"))
    assert mm.ensure_model_available(allow_download=False) is None


def test_after_onboarding_a_tested_model_on_disk_is_reported(models_dir, monkeypatch):
    _put(models_dir, DEEPSEEK)
    qwen = _put(models_dir, QWEN_7B)
    monkeypatch.setattr(mm, "_download_from_hf", lambda *a, **k: pytest.fail("no download after onboarding"))
    assert mm.ensure_model_available(allow_download=False) == str(qwen)


def test_onboarding_never_takes_the_untested_file_the_launcher_left(models_dir, monkeypatch):
    deepseek = _put(models_dir, DEEPSEEK)
    monkeypatch.setenv("LLAMA_CPP_MODEL_PATH", str(deepseek))
    assert llm_service.catalog_model_on_disk() is None
    gemma = _put(models_dir, GEMMA_12B)
    assert llm_service.catalog_model_on_disk() == gemma


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """The launcher module with its home folder in a temporary directory.

    pywebview is stubbed as in the other launcher tests.
    """
    sys.modules.setdefault("webview", types.SimpleNamespace(create_window=lambda *a, **kw: None, start=lambda *a, **kw: None))
    from desktop import launcher as launcher_module
    monkeypatch.setattr(launcher_module, "_SPENDIFAI_HOME", tmp_path)
    return launcher_module


def test_launcher_reads_onboarding_done_from_the_ledger(launcher, tmp_path):
    assert launcher._onboarding_done() is False
    with sqlite3.connect(str(tmp_path / "ledger.db")) as conn:
        conn.execute("CREATE TABLE user_settings (key VARCHAR(64) PRIMARY KEY, value VARCHAR(255))")
        conn.execute("INSERT INTO user_settings VALUES ('onboarding_done', 'true')")
    assert launcher._onboarding_done() is True


def test_launcher_clears_an_untested_path_when_no_tested_model_is_on_disk(launcher, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"LLAMA_CPP_MODEL_PATH=/models/{DEEPSEEK}\nOTHER=1\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_CPP_MODEL_PATH", f"/models/{DEEPSEEK}")  # restored after the test
    monkeypatch.setattr(launcher, "_write_status", lambda status: None)
    monkeypatch.setattr("core.model_manager.ensure_model_available", lambda progress_callback=None, allow_download=True: None)
    launcher._download_model_bg(tmp_path, allow_download=False)
    assert "LLAMA_CPP_MODEL_PATH=\n" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert os.environ["LLAMA_CPP_MODEL_PATH"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# Out of catalog and the notice (spec 36, 4 and 5)
# ─────────────────────────────────────────────────────────────────────────────

def test_user_path_to_untested_model_raises_the_notice(models_dir):
    deepseek = _put(models_dir, DEEPSEEK)
    settings = {"llm_backend": "local_llama_cpp", "llama_cpp_model_path": str(deepseek)}
    notice = llm_service.out_of_catalog_notice(settings)
    assert notice.model_names == ("DeepSeek R1 Distill Qwen 7B",)
    assert notice.recommended_name == "Gemma 3 12B"
    assert notice.recommended_path == ""          # not on disk: "Download and use"
    assert notice.setting_keys == ("llama_cpp_model_path",)


def test_tested_model_raises_no_notice(models_dir):
    gemma = _put(models_dir, GEMMA_12B)
    settings = {"llm_backend": "local_llama_cpp", "llama_cpp_model_path": str(gemma)}
    assert llm_service.out_of_catalog_notice(settings) is None


def test_automatic_choice_of_an_untested_file_raises_the_notice(models_dir):
    _put(models_dir, DEEPSEEK)
    notice = llm_service.out_of_catalog_notice({"llm_backend": "local_llama_cpp", "llama_cpp_model_path": ""})
    assert notice is not None and notice.setting_keys == ("llama_cpp_model_path",)


def test_a_phase_with_its_own_untested_model_is_named(models_dir):
    gemma = _put(models_dir, GEMMA_12B)
    deepseek = _put(models_dir, DEEPSEEK)
    settings = {
        "llm_backend": "local_llama_cpp", "llama_cpp_model_path": str(gemma),
        "cleaner_llm_backend": "local_llama_cpp", "cleaner_llama_cpp_model_path": str(deepseek),
    }
    notice = llm_service.out_of_catalog_notice(settings)
    assert notice.setting_keys == ("cleaner_llama_cpp_model_path",)
    assert notice.recommended_path == str(gemma)  # on disk: "Use"


def test_remote_backend_raises_no_notice(models_dir):
    _put(models_dir, DEEPSEEK)
    assert llm_service.out_of_catalog_notice({"llm_backend": "openai", "llama_cpp_model_path": ""}) is None


class _FakeSettings:
    """In-memory stand-in for SettingsService: only set and set_bulk are used.

    Attributes:
        values: the settings written so far.
    """

    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def set_bulk(self, settings: dict[str, str]) -> None:
        self.values.update(settings)


def test_use_points_the_named_settings_at_the_recommended_model(models_dir):
    deepseek = _put(models_dir, DEEPSEEK)
    gemma = _put(models_dir, GEMMA_12B)
    store = _FakeSettings({"llm_backend": "local_llama_cpp", "llama_cpp_model_path": str(deepseek)})
    notice = llm_service.out_of_catalog_notice(store.values)
    llm_service.use_recommended_model(store, notice, str(gemma))
    assert store.values["llama_cpp_model_path"] == str(gemma)
    assert llm_service.out_of_catalog_notice(store.values) is None


def test_keep_mine_silences_the_same_file_only(models_dir):
    deepseek = _put(models_dir, DEEPSEEK)
    other = _put(models_dir, "other-untested.gguf")
    store = _FakeSettings({"llm_backend": "local_llama_cpp", "llama_cpp_model_path": str(deepseek)})
    llm_service.keep_untested_model(store, llm_service.out_of_catalog_notice(store.values))
    assert llm_service.out_of_catalog_notice(store.values) is None
    store.values["llama_cpp_model_path"] = str(other)
    assert llm_service.out_of_catalog_notice(store.values) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Picker badges (spec 36, 6) and report (spec 36, 7)
# ─────────────────────────────────────────────────────────────────────────────

def test_picker_badges(models_dir):
    assert llm_service.model_badge(GEMMA_12B) == "recommended"
    assert llm_service.model_badge(QWEN_7B) == ""
    assert llm_service.model_badge(DEEPSEEK) == "untested"
    assert llm_service.model_badge("Qwen_Qwen3.5-9B-Q4_K_M") == "untested"


def test_report_says_who_chose_the_model_and_whether_it_is_tested(models_dir):
    from services import diagnostics_service
    deepseek = _put(models_dir, DEEPSEEK)
    settings = {"llm_backend": "local_llama_cpp", "llama_cpp_model_path": str(deepseek)}
    in_use, fields = diagnostics_service._model_in_use(settings)
    assert fields == {"model_choice": "user", "model_in_catalog": False, "recommended_model": "Gemma 3 12B"}
    phases = diagnostics_service._phase_settings(settings, in_use)
    assert phases["categorizer"]["model"] == DEEPSEEK
    assert phases["categorizer"]["in_catalog"] is False
