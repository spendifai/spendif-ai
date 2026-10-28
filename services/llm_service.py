"""LLM service — facade for LLM backend operations used by the UI.

Keeps the coupling gate clean: UI files import only from services.*,
never from core.llm_backends or core.model_manager directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GgufModelInfo:
    """Unified catalog entry merging static metadata with local-disk status."""
    name: str               # catalog key / file stem (e.g. "Qwen_Qwen3.5-9B-Q4_K_M")
    url: str                # download URL
    size_gb: float          # catalog size estimate (GB)
    description: str        # human-readable description
    downloaded: bool        # True when the .gguf file is present locally
    local_path: str         # absolute path when downloaded, else ""
    local_size_gb: float | None  # actual file size when downloaded


def get_gguf_catalog() -> list[GgufModelInfo]:
    """Return every catalog model merged with its local-disk status.

    Single source of truth for the picker AND the download section — no more
    ad-hoc merge logic scattered across the UI.
    """
    from core.llm_backends import DEFAULT_GGUF_MODELS
    models_dir = Path.home() / ".spendifai" / "models"

    # Build a case-insensitive map: lowercase_stem → {name, path, size_gb}
    local_by_lower: dict[str, dict] = {}
    if models_dir.exists():
        for p in sorted(models_dir.glob("*.gguf")):
            local_by_lower[p.stem.lower()] = {
                "name": p.stem,
                "path": str(p),
                "size_gb": round(p.stat().st_size / (1024 ** 3), 2),
            }

    result = []
    for name, meta in DEFAULT_GGUF_MODELS.items():
        local = local_by_lower.get(name.lower())
        result.append(GgufModelInfo(
            name=name,
            url=meta.get("url", ""),
            size_gb=float(meta.get("size_gb", 0)),
            description=meta.get("description", ""),
            downloaded=local is not None,
            local_path=local["path"] if local else "",
            local_size_gb=local["size_gb"] if local else None,
        ))
    return result


@dataclass(frozen=True)
class LLMTestResult:
    """Result of a backend test. `severity='warning'` signals a recoverable
    failure with a known fix in `hint_command`."""
    ok: bool
    message: str
    severity: str = "error"      # "error" | "warning"
    hint_command: str | None = None


# Recoverable failure patterns: (regex on exception text, hint command).
# When matched, the UI shows a warning + actionable suggestion instead of an error.
_RECOVERABLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Qwen 3.5 / future SSM-hybrid models — wheel PyPI corrente non li carica.
    (re.compile(r"missing tensor 'blk\.\d+\.ssm_", re.IGNORECASE),
     "bash scripts/setup_ssm_build.sh"),
    (re.compile(r"unknown model architecture:\s*['\"]?qwen3", re.IGNORECASE),
     "bash scripts/setup_ssm_build.sh"),
    # Gemma 4 — basta aggiornare la wheel PyPI standard.
    (re.compile(r"unknown model architecture:\s*['\"]?gemma4", re.IGNORECASE),
     "uv pip install --upgrade llama-cpp-python"),
)


def _classify_exception(exc_text: str) -> tuple[str, str | None]:
    """Map an exception message to (severity, hint_command)."""
    for pattern, hint in _RECOVERABLE_PATTERNS:
        if pattern.search(exc_text):
            return "warning", hint
    return "error", None


# ── Context-length detection ─────────────────────────────────────────────────

def detect_llama_cpp_context(model_path: str = "") -> int | None:
    """Read GGUF context length. If *model_path* is empty, uses the default."""
    from core.llm_backends import LlamaCppBackend
    if not model_path:
        try:
            model_path = LlamaCppBackend._default_model_path()
        except Exception:
            return None
    return LlamaCppBackend.read_gguf_context_length(model_path)


def recommended_llama_cpp_context(model_path: str = "") -> int | None:
    """Contesto da impostare davvero: quello del modello, ma con il tetto.

    ``detect_llama_cpp_context`` restituisce la capacita' dichiarata dal file,
    che su un modello moderno sono 131072 token. Impostarla per intero non e'
    gratis: la cache delle chiavi cresce con il contesto e si mangia la memoria
    della macchina. Il tetto ``DEFAULT_N_CTX_CAP`` viene dai dati del
    benchmark ed e' 2,3 volte il prompt piu' grande mai osservato.

    Esiste perche' il valore veniva deciso in due punti che non si parlavano:
    l'onboarding ne scriveva uno fisso, troppo piccolo per i nostri stessi
    prompt, e la pagina delle impostazioni proponeva quello pieno, troppo
    grande per la macchina. Da qui in poi il numero si calcola in un posto solo.
    """
    from core.llm_backends import LlamaCppBackend

    detected = detect_llama_cpp_context(model_path)
    if not detected:
        return None
    return min(detected, LlamaCppBackend.DEFAULT_N_CTX_CAP)


def recommended_gpu_layers() -> int:
    """How many layers to put on the accelerator: all of them, or none.

    Returns -1 (every layer on the accelerator) when the inference library has
    an accelerator it can actually address, and 0 otherwise.

    The onboarding used to write 0 flat, with a note saying the user could opt
    in through Settings. Nothing told them the setting existed, so on a machine
    with a working accelerator the first import ran on the processor and took
    hours instead of minutes. Meanwhile the per-phase defaults all said -1, so
    the two halves of the same configuration disagreed.

    The context window is decided the same way two lines above: by asking,
    once, in one place. This is that, for the accelerator.
    """
    from core import runtime_info

    return -1 if runtime_info.accelerator_available() else 0


# Every setting that says how many layers go on the accelerator: the main
# backend, the legacy categorizer slot and the four phases.
GPU_LAYER_SETTINGS = (
    "llama_cpp_n_gpu_layers",
    "cat_llama_cpp_n_gpu_layers",
    "classifier_llama_cpp_n_gpu_layers",
    "cleaner_llama_cpp_n_gpu_layers",
    "categorizer_llama_cpp_n_gpu_layers",
    "footer_llama_cpp_n_gpu_layers",
)

# Set once the layers written by the old onboarding have been looked at.
GPU_LAYERS_REVIEWED = "gpu_layers_reviewed"
_layers_checked: set[str] = set()


def restore_accelerator_layers(engine) -> list[str]:
    """Undo the 0 layers the old onboarding wrote, once, where it was wrong.

    Until 2026-10-23 the onboarding wrote 0 flat, so every installation
    analysed on the processor, five to eight times slower, even with a card
    the library could use. Fixing the onboarding helps new installations
    only: this is for the ones that already went through it.

    A 0 becomes -1 only when the library can address an accelerator, which is
    the same question the onboarding asks now. Somebody who chose 0 on
    purpose cannot be told apart from somebody who got it from the
    onboarding; the second case is every installation, the first is rare and
    a setting away. The review is recorded only once it has run with an
    accelerator present, so a later 0 chosen by hand is left alone, and a
    machine that has no accelerator yet is asked again at the next start.

    Runs once per process: app.py calls it on every rerun.

    Returns the settings it changed.
    """
    from db.models import get_session
    from db.repository import get_user_setting, set_user_setting

    key = str(engine.url)
    if key in _layers_checked:
        return []
    _layers_checked.add(key)
    with get_session(engine) as session:
        if get_user_setting(session, GPU_LAYERS_REVIEWED) == "true":
            return []
        if recommended_gpu_layers() != -1:
            return []
        changed = [
            key for key in GPU_LAYER_SETTINGS
            if (get_user_setting(session, key) or "").strip() == "0"
        ]
        for key in changed:
            set_user_setting(session, key, "-1")
        set_user_setting(session, GPU_LAYERS_REVIEWED, "true")
        session.commit()
        return changed


def detect_ollama_context(model: str, base_url: str = "http://localhost:11434") -> int | None:
    """Query Ollama /api/show for the model's context length."""
    from core.llm_backends import OllamaBackend
    return OllamaBackend.fetch_context_length(model, base_url)


def get_known_context_window(model: str) -> int | None:
    """Lookup a known context window for OpenAI / Claude models."""
    from core.llm_backends import _KNOWN_CONTEXT
    return _KNOWN_CONTEXT.get(model)


def detect_vllm_context(base_url: str, model: str) -> int | None:
    """Query vLLM /v1/models for the model's context length."""
    from core.llm_backends import VllmBackend
    return VllmBackend.fetch_context_length(base_url, model)


# ── LLM test / validation ───────────────────────────────────────────────────

def test_llm_backend(
    backend: str,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    **extra_kwargs: Any,
) -> LLMTestResult:
    """Send a minimal test prompt.

    Returns an `LLMTestResult` with severity='warning' (+ hint_command) when
    the failure matches a known recoverable pattern (e.g. SSM-architecture
    model on a llama-cpp-python wheel that doesn't support it).
    """
    from core.llm_backends import BackendFactory, LLMValidationError

    try:
        kwargs: dict = {"timeout": 15}
        if backend == "local_llama_cpp":
            kwargs.pop("timeout", None)
            kwargs.update(extra_kwargs)
        elif backend == "local_ollama":
            kwargs["base_url"] = base_url
            kwargs["model"] = model
        elif backend == "openai":
            kwargs["api_key"] = api_key
            kwargs["model"] = model
        elif backend == "claude":
            kwargs["api_key"] = api_key
            kwargs["model"] = model
        elif backend in ("vllm", "vllm_offline"):
            kwargs["base_url"] = base_url
            kwargs["model"] = model
            kwargs["api_key"] = api_key or "none"

        be = BackendFactory.create(backend, **kwargs)
        resp = be.complete_structured(
            system_prompt="You are a test assistant.",
            user_prompt="Reply with exactly: OK",
            json_schema={"type": "object", "properties": {"reply": {"type": "string"}}},
        )
        return LLMTestResult(ok=True, message=str(resp))
    except LLMValidationError as exc:
        return LLMTestResult(ok=False, message=f"Validation: {exc}")
    except Exception as exc:
        text = str(exc)
        severity, hint = _classify_exception(text)
        return LLMTestResult(ok=False, message=text, severity=severity, hint_command=hint)


# ── Local model listing ──────────────────────────────────────────────────────

def list_local_llama_cpp_models() -> list[dict[str, Any]]:
    """List GGUF models in ~/.spendifai/models/ and the default model dir."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend.list_local_models()


def list_ollama_models(base_url: str = "http://localhost:11434", timeout: int = 5) -> list[str]:
    """Return tags of models installed on the Ollama server. Empty list when
    Ollama isn't reachable or no models are pulled — caller decides whether
    to render a free-text fallback.
    """
    import urllib.error
    import urllib.request
    import json as _json
    url = base_url.rstrip("/") + "/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []
    models = data.get("models") or []
    return sorted({m.get("name", "") for m in models if m.get("name")})


# Curated fallback used when the live API can't be queried (no key, network
# failure, rate-limited). Update as new flagship models ship.
KNOWN_OPENAI_MODELS = (
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo",
    "o1-mini",
)

KNOWN_ANTHROPIC_MODELS = (
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5",
    "claude-opus-4-7",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
)


def list_openai_models(api_key: str = "", timeout: int = 5) -> list[str]:
    """List OpenAI chat-completion models via the live `/v1/models` endpoint.

    Filters down to GPT/O-series identifiers (skips embeddings, whisper,
    image, tts, etc.). Returns the curated fallback when no key is set
    or the request fails.
    """
    if not api_key:
        return list(KNOWN_OPENAI_MODELS)
    import urllib.error
    import urllib.request
    import json as _json
    req = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return list(KNOWN_OPENAI_MODELS)
    ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    # Keep only chat-completion candidates (gpt-*, o1-*, o3-*) — drop
    # embeddings, whisper, tts, dalle, etc. that would clutter the UI.
    chat = [i for i in ids if i.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-"))]
    return sorted(chat) if chat else list(KNOWN_OPENAI_MODELS)


def list_anthropic_models(api_key: str = "", timeout: int = 5) -> list[str]:
    """List Anthropic chat models via the live `/v1/models` endpoint.

    Returns the curated fallback when no key is set or the request fails.
    """
    if not api_key:
        return list(KNOWN_ANTHROPIC_MODELS)
    import urllib.error
    import urllib.request
    import json as _json
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return list(KNOWN_ANTHROPIC_MODELS)
    ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    return sorted(ids) if ids else list(KNOWN_ANTHROPIC_MODELS)


def get_default_gguf_models() -> dict:
    """Return the DEFAULT_GGUF_MODELS dict for the download UI."""
    from core.llm_backends import DEFAULT_GGUF_MODELS
    return DEFAULT_GGUF_MODELS


def get_llama_cpp_default_model_path() -> str:
    """Return the default model path for llama.cpp."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend._default_model_path()


def read_gguf_context_length(path: str) -> int | None:
    """Read context length from a GGUF file's metadata."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend.read_gguf_context_length(path)


def download_gguf_model(url: str, dest: str, progress_callback=None) -> str:
    """Download a GGUF model file. Returns the destination path."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend.download_model(url, dest, progress_callback)


# ── Backend factory ──────────────────────────────────────────────────────────

def create_backend(backend_name: str, **kwargs):
    """Create an LLM backend instance via BackendFactory."""
    from core.llm_backends import BackendFactory
    return BackendFactory.create(backend_name, **kwargs)


# Re-export LLMValidationError so UI can catch it without importing core
def _get_validation_error_class():
    from core.llm_backends import LLMValidationError
    return LLMValidationError

# Lazy re-export: importable as `from services.llm_service import LLMValidationError`
try:
    from core.llm_backends import LLMValidationError
except ImportError:
    pass


# ── Hardware detection + model recommendation ────────────────────────────────

def detect_system_hardware() -> dict[str, Any]:
    """Detect OS, RAM, GPU, VRAM."""
    from core.model_manager import detect_hw
    return detect_hw()


def list_available_models() -> list[Path]:
    """List GGUF files in ~/.spendifai/models/."""
    from core.model_manager import list_local_models
    return list_local_models()


def catalog_model_on_disk() -> Path | None:
    """The tested model the onboarding announces and saves (spec 36, 3.1).

    The path the launcher wrote (LLAMA_CPP_MODEL_PATH) when it is a registry
    model, otherwise the automatic choice when that is one. A file outside
    the catalog is never returned: the onboarding avoids the case instead of
    warning about it.

    Returns:
        The model file, or None when no registry model is on disk yet.
    """
    import os

    from core.model_manager import in_catalog, resolve_local_model
    from_launcher = os.environ.get("LLAMA_CPP_MODEL_PATH", "")
    if from_launcher and in_catalog(from_launcher) and Path(from_launcher).exists():
        return Path(from_launcher)
    chosen = resolve_local_model()
    return chosen if chosen is not None and in_catalog(chosen) else None


def get_recommended_model(hw: dict[str, Any]):
    """Return the ModelInfo the first launch downloads on this hardware, or None."""
    from core.model_manager import recommended_chain
    chain = recommended_chain(hw)
    return chain[0] if chain else None


# ── History engine ───────────────────────────────────────────────────────────

def get_description_profiles(engine) -> list:
    """Fetch description profiles (for analytics associations chart)."""
    from core.history_engine import get_description_profiles as _get_profiles
    from db.models import get_session
    with get_session(engine) as session:
        return _get_profiles(session)


# ── Local model in use, and the out-of-catalog notice (spec 36) ──────────────

_LOCAL_BACKEND = "local_llama_cpp"
_PHASES = ("classifier", "cleaner", "categorizer", "footer")
_KEPT_SETTING = "model_out_of_catalog_kept"


@dataclass(frozen=True)
class LocalModelInUse:
    """The local model one part of the pipeline runs on.

    Attributes:
        phase: "main" or a phase with its own backend (classifier, cleaner,
            categorizer, footer).
        path: the model file actually loaded; "" when the folder is empty.
        setting_key: the setting that names the file; "" when nobody named
            it and the automatic choice decides (spec 36, 3).
        in_catalog: whether the file is a tested registry model.
    """
    phase: str
    path: str
    setting_key: str
    in_catalog: bool

    @property
    def chosen_by(self) -> str:
        """'user' when a setting names the file, 'automatic' otherwise."""
        return "user" if self.setting_key else "automatic"


def local_models_in_use(settings: dict[str, str]) -> list[LocalModelInUse]:
    """Every local model the pipeline would load with these settings.

    Mirrors how the import builds its backends (core.orchestrator): a phase
    without its own backend runs on the main one; a phase with its own local
    backend takes its own file, then the legacy cat_* one for the
    categorizer, then the main file; an empty path is the automatic choice.

    Args:
        settings: the user settings, as returned by SettingsService.get_all().

    Returns:
        One entry for the main backend when it is local, plus one per phase
        with its own local backend.
    """
    from core.model_manager import in_catalog, resolve_local_model

    automatic = resolve_local_model()
    automatic_path = str(automatic) if automatic else ""

    def entry(phase: str, keys: list[str]) -> LocalModelInUse:
        for key in keys:
            if settings.get(key):
                return LocalModelInUse(phase, settings[key], key, in_catalog(settings[key]))
        return LocalModelInUse(phase, automatic_path, "", in_catalog(automatic_path))

    in_use: list[LocalModelInUse] = []
    if settings.get("llm_backend", "") == _LOCAL_BACKEND:
        in_use.append(entry("main", ["llama_cpp_model_path"]))
    for phase in _PHASES:
        backend = settings.get(f"{phase}_llm_backend", "") or (settings.get("cat_llm_backend", "") if phase == "categorizer" else "")
        if backend != _LOCAL_BACKEND:
            continue
        keys = [f"{phase}_llama_cpp_model_path"] + (["cat_llama_cpp_model_path"] if phase == "categorizer" else []) + ["llama_cpp_model_path"]
        in_use.append(entry(phase, keys))
    return in_use


def model_display_name(model_file: str) -> str:
    """A model file name as a person reads it: no extension, quantisation or "instruct", spaces.

    Args:
        model_file: path or file name of a .gguf file.
    """
    stem = re.sub(r"\.gguf$", "", Path(model_file).name, flags=re.IGNORECASE)
    stem = re.sub(r"[-_.]?(Q\d(_K)?(_[SML])?|IQ\d\w*|F16|BF16)$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^(google|bartowski|Qwen)_", "", stem)
    stem = re.sub(r"[-_](it|instruct)$", "", stem, flags=re.IGNORECASE)
    return re.sub(r"[-_]+", " ", stem).strip()


@dataclass(frozen=True)
class OutOfCatalogNotice:
    """What the out-of-catalog notice says and offers (spec 36, 5).

    Attributes:
        model_files: file names of the untested models in use, sorted.
        model_names: the same, as a person reads them.
        recommended_name: name of the model recommended for this computer.
        recommended_path: its file when already on disk; "" when it must be
            downloaded first.
        recommended_size_gb: its download size, in GB.
        setting_keys: the settings to point at the recommended model when the
            person accepts.
    """
    model_files: tuple[str, ...]
    model_names: tuple[str, ...]
    recommended_name: str
    recommended_path: str
    recommended_size_gb: float
    setting_keys: tuple[str, ...]


def out_of_catalog_notice(settings: dict[str, str]) -> OutOfCatalogNotice | None:
    """The notice to show, or None when every local model in use is tested or kept.

    Args:
        settings: the user settings, as returned by SettingsService.get_all().
    """
    from core.model_manager import cached_hw, local_catalog_model, recommended_chain

    outside = [m for m in local_models_in_use(settings) if m.path and not m.in_catalog]
    if not outside:
        return None
    files = tuple(sorted({Path(m.path).name for m in outside}))
    if settings.get(_KEPT_SETTING, "") == "|".join(files):
        return None
    chain = recommended_chain(cached_hw())
    if not chain:
        return None
    recommended = chain[0]
    on_disk = local_catalog_model(recommended)
    # Nobody named the file: the automatic choice lives in the main setting.
    keys = tuple(sorted({m.setting_key or "llama_cpp_model_path" for m in outside}))
    return OutOfCatalogNotice(
        model_files=files,
        model_names=tuple(model_display_name(f) for f in files),
        recommended_name=model_display_name(recommended.name),
        recommended_path=str(on_disk) if on_disk else "",
        recommended_size_gb=round(recommended.size_mb / 1000, 1),
        setting_keys=keys,
    )


def use_recommended_model(settings_svc: Any, notice: OutOfCatalogNotice, model_path: str) -> None:
    """Point every setting that named an untested model at the recommended one.

    Args:
        settings_svc: a SettingsService.
        notice: the notice the person answered.
        model_path: the recommended model's file, on disk.
    """
    settings_svc.set_bulk({key: model_path for key in notice.setting_keys} | {_KEPT_SETTING: ""})


def keep_untested_model(settings_svc: Any, notice: OutOfCatalogNotice) -> None:
    """Remember "keep mine": the notice does not come back for these same files.

    Args:
        settings_svc: a SettingsService.
        notice: the notice the person answered.
    """
    settings_svc.set(_KEPT_SETTING, "|".join(notice.model_files))


def download_recommended_model(progress_callback=None) -> str | None:
    """Download the model recommended for this computer, with its fallbacks.

    Args:
        progress_callback: called with (fraction 0..1, message); None for none.

    Returns:
        The downloaded file, or None when every attempt failed.
    """
    from core.model_manager import ensure_model_available
    return ensure_model_available(progress_callback=progress_callback, allow_download=True)


def model_badge(model_file: str) -> str:
    """How the model picker tags a model (spec 36, 6).

    Args:
        model_file: path or file name of a .gguf file.

    Returns:
        "recommended" for the model recommended for this computer,
        "untested" for a model outside the registry, "" otherwise.
    """
    from core.model_manager import cached_hw, in_catalog, recommended_chain

    chain = recommended_chain(cached_hw())
    file_name = Path(model_file).name.lower()
    if not file_name.endswith(".gguf"):
        file_name += ".gguf"
    if chain and file_name == chain[0].filename.lower():
        return "recommended"
    return "" if in_catalog(file_name) else "untested"


def local_models_outside_picker_list() -> list[Path]:
    """Model files on disk that the picker's download list does not know.

    Without them the picker could not show what is actually in the folder,
    including a recommended registry model missing from that list.
    """
    from core.llm_backends import DEFAULT_GGUF_MODELS
    from core.model_manager import list_local_models

    known = {name.lower() for name in DEFAULT_GGUF_MODELS}
    return [f for f in list_local_models() if f.stem.lower() not in known]
