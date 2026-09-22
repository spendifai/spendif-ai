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


def get_recommended_model(ram_gb: int):
    """Return the best ModelInfo for the given RAM, or None."""
    from config import get_recommended_model as _get_rec
    return _get_rec(ram_gb)


# ── History engine ───────────────────────────────────────────────────────────

def get_description_profiles(engine) -> list:
    """Fetch description profiles (for analytics associations chart)."""
    from core.history_engine import get_description_profiles as _get_profiles
    from db.models import get_session
    with get_session(engine) as session:
        return _get_profiles(session)
