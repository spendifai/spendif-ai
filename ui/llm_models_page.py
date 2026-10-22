"""LLM Models page — dedicated configuration for the 4 phase-specific backends.

AI-96: promotes LLM configuration from a Settings sub-section to a top-level
navigation entry. Each pipeline phase (classifier, cleaner, categorizer,
footer) gets its own backend slot; account-level credentials (API keys,
base URLs) are shared across phases and live in a single section at the top.

PR-1 (this file): minimum-viable structure — current status, account
credentials, four per-phase configuration cards, persistence to user_settings.
Operations (test, calibrate, stats, download) and Settings cleanup are
follow-ups.
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


# ── Privacy state of the selected LLM backend (AI-229) ───────────────────────
# Honest classification of where the user's data goes. Local backends never
# touch the internet (green); hosted providers send data in clear text (red).
# When the payload-redaction feature ships, flip REDACTION_AVAILABLE to True so
# hosted backends surface as "orange" (data leaves but is redacted) instead.
REDACTION_AVAILABLE = False

# Backends that always run on the user's machine.
_LOCAL_BACKENDS = frozenset({"local_llama_cpp", "vllm_offline"})
# Backends that always reach an external provider over the internet.
_REMOTE_BACKENDS = frozenset({"openai", "claude"})
# Backends whose destination depends on the configured URL (local server vs
# remote). Maps backend → the settings key holding its base URL.
_URL_DEPENDENT_BACKENDS = {
    "local_ollama": "ollama_base_url",
    "openai_compatible": "compat_base_url",
    "vllm": "vllm_base_url",
}

# state → (status colour of the app palette, icon)
_PRIVACY_BAR_STYLE = {
    "green":  ("status_ok", "🔒"),
    "orange": ("status_warn", "🔐"),
    "red":    ("status_danger", "⚠️"),
}


def _translucent(color: str, alpha: float = 0.12) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _url_is_local(url: str) -> bool:
    """True if ``url`` points to the local machine or a private LAN address.

    Empty or unparseable URLs count as NOT local: a privacy claim must never
    over-promise, so an unconfigured endpoint is treated as remote.
    """
    if not url or not url.strip():
        return False
    raw = url.strip()
    host = urlparse(raw if "://" in raw else f"http://{raw}").hostname
    if not host:
        return False
    host = host.lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a real hostname → remote
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _remote_state() -> str:
    """State for a backend that reaches the internet: orange if the payload is
    redacted (feature shipped), otherwise red (clear text)."""
    return "orange" if REDACTION_AVAILABLE else "red"


def _privacy_state(backend: str, settings: dict) -> str:
    """Classify the selected backend as ``'green'`` | ``'orange'`` | ``'red'``."""
    if backend in _LOCAL_BACKENDS:
        return "green"
    if backend in _REMOTE_BACKENDS:
        return _remote_state()
    if backend in _URL_DEPENDENT_BACKENDS:
        default = "http://localhost:11434" if backend == "local_ollama" else ""
        url = settings.get(_URL_DEPENDENT_BACKENDS[backend], default)
        return "green" if _url_is_local(url) else _remote_state()
    # Unknown backend → be conservative, assume it reaches the internet.
    return _remote_state()


def _render_privacy_bar(backend: str, settings: dict) -> None:
    """Render the traffic-light privacy bar for the selected main backend."""
    state = _privacy_state(backend, settings)
    token, icon = _PRIVACY_BAR_STYLE[state]
    color = SettingsService.value_colors()[token]
    bg = _translucent(color)
    msg = t(f"llm_models.privacy.{state}")
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:.6rem;
                    background:{bg};border:1px solid {color};
                    border-left:6px solid {color};border-radius:8px;
                    padding:.7rem 1rem;margin:.5rem 0;">
          <span style="font-size:1.2rem;line-height:1;">{icon}</span>
          <span style="font-size:.92rem;line-height:1.4;">{msg}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# Backends supported by the runtime (`_build_phase_backend` in
# core/orchestrator.py). Order chosen so the most-common-locally first.
_SUPPORTED_BACKENDS = (
    "local_llama_cpp",
    "local_ollama",
    "openai",
    "claude",
    "openai_compatible",
    "vllm",
    "vllm_offline",
)

_PHASES = (
    ("classifier",  "🔍"),
    ("cleaner",     "✂️"),
    ("categorizer", "🏷️"),
    ("footer",      "📐"),
)


def _backend_label(b: str) -> str:
    return t(f"llm_models.backend.{b}")


def _backend_options_inherit_first() -> list[str]:
    # Empty string = "inherit from main backend".
    return ["", *_SUPPORTED_BACKENDS]


def _model_picker(
    backend: str,
    current_value: str,
    settings: dict,
    widget_key: str,
    placeholder_label: str,
) -> str:
    """Render a backend-aware model selector and return the chosen identifier.

    For backends with a discoverable catalog (local_llama_cpp, local_ollama,
    openai, claude) → selectbox populated dynamically. For backends without
    a useful catalog (openai_compatible, vllm, vllm_offline) → text_input
    fallback. Always offers a "(custom)" option so power users can type a
    value the catalog doesn't know about yet.
    """
    from pathlib import Path

    if backend == "local_llama_cpp":
        from services.llm_service import get_gguf_catalog
        catalog = get_gguf_catalog()
        # Picker: all catalog models, downloaded ones selectable, others labelled ⬇
        options_paths: list[str] = []
        labels: dict[str, str] = {}
        for m in catalog:
            # Use local path for downloaded models; catalog name as placeholder otherwise
            key = m.local_path if m.downloaded else m.name
            options_paths.append(key)
            size = m.local_size_gb if m.local_size_gb is not None else m.size_gb
            if m.downloaded:
                labels[key] = f"✓ {m.name} ({size} GB)"
            else:
                labels[key] = f"⬇ {m.name} ({size} GB) — da scaricare"
        return _selectbox_or_custom(
            options=options_paths,
            current=current_value,
            labels=labels,
            empty_hint=t("llm_models.model_picker.no_local_gguf"),
            widget_key=widget_key,
        )

    if backend == "local_ollama":
        from services.llm_service import list_ollama_models
        base_url = settings.get("ollama_base_url", "http://localhost:11434")
        models = list_ollama_models(base_url)
        return _selectbox_or_custom(
            options=models,
            current=current_value,
            empty_hint=t("llm_models.model_picker.no_ollama", url=base_url),
            widget_key=widget_key,
        )

    if backend == "openai":
        from services.llm_service import list_openai_models
        models = list_openai_models(api_key=settings.get("openai_api_key", ""))
        return _selectbox_or_custom(
            options=models,
            current=current_value,
            empty_hint="",
            widget_key=widget_key,
        )

    if backend == "claude":
        from services.llm_service import list_anthropic_models
        models = list_anthropic_models(api_key=settings.get("anthropic_api_key", ""))
        return _selectbox_or_custom(
            options=models,
            current=current_value,
            empty_hint="",
            widget_key=widget_key,
        )

    # openai_compatible / vllm / vllm_offline — no curated catalog,
    # depends on the user's deployment. Free-text input only.
    return st.text_input(
        placeholder_label,
        value=current_value,
        key=widget_key,
    )


def _selectbox_or_custom(
    options: list[str],
    current: str,
    widget_key: str,
    labels: dict | None = None,
    empty_hint: str = "",
) -> str:
    """Render a selectbox with a `(custom)` escape hatch.

    Pre-selects `current` when it's in `options`; falls back to a free
    text input below when the user picks `(custom)` or when the catalog
    is empty (e.g. Ollama not running, no GGUF in models dir).
    """
    _CUSTOM = t("llm_models.model_picker.custom_option")

    if not options:
        if empty_hint:
            st.caption(empty_hint)
        return st.text_input(
            t("llm_models.model_picker.custom_label"),
            value=current,
            key=widget_key + "_text",
        )

    display = [_CUSTOM, *options]
    labels = labels or {}
    fmt = lambda v: _CUSTOM if v == _CUSTOM else labels.get(v, v)

    # Default: current value if it's in options, else "(custom)"
    if current and current in options:
        idx = display.index(current)
    else:
        idx = 0
    sel = st.selectbox(
        t("llm_models.model_picker.label"),
        options=display,
        format_func=fmt,
        index=idx,
        key=widget_key + "_select",
    )
    if sel == _CUSTOM:
        return st.text_input(
            t("llm_models.model_picker.custom_label"),
            value=current,
            key=widget_key + "_text",
        )
    return sel


def _current_phase_backend(settings: dict, phase: str) -> str:
    """Resolve the effective backend for a phase, with legacy fallbacks.

    Mirrors the logic of `_build_phase_backend` so the UI shows what the
    runtime will actually use.
    """
    val = settings.get(f"{phase}_llm_backend", "") or ""
    if not val and phase == "categorizer":
        val = settings.get("cat_llm_backend", "") or ""
    if not val:
        val = settings.get("llm_backend", "") or ""
    return val


def _render_current_status(settings: dict) -> None:
    st.subheader(t("llm_models.status.title"))
    rows = []
    for phase, icon in _PHASES:
        backend = _current_phase_backend(settings, phase)
        rows.append(
            f"- {icon} **{t(f'llm_models.phase.{phase}')}** — "
            f"`{_backend_label(backend) if backend else '—'}`"
        )
    st.markdown("\n".join(rows))


def _render_main_backend(svc: SettingsService, settings: dict) -> None:
    """The main backend acts as the fallback for every phase that doesn't
    override it. Configuring this is the FIRST thing a user does — the
    four phase cards below default to inheriting from it.
    """
    st.subheader(t("llm_models.main.title"))
    st.caption(t("llm_models.main.caption"))

    current = settings.get("llm_backend", "local_llama_cpp")
    idx = _SUPPORTED_BACKENDS.index(current) if current in _SUPPORTED_BACKENDS else 0
    sel = st.selectbox(
        t("llm_models.main.backend"),
        options=_SUPPORTED_BACKENDS,
        format_func=_backend_label,
        index=idx,
        key="_llmpage_main_backend",
    )

    new_vals: dict[str, str] = {"llm_backend": sel}

    if sel == "local_llama_cpp":
        new_vals["llama_cpp_model_path"] = _model_picker(
            backend=sel,
            current_value=settings.get("llama_cpp_model_path", ""),
            settings=settings,
            widget_key="_llmpage_main_lcpp",
            placeholder_label=t("llm_models.phase.local_llama_cpp.model_path"),
        )
        with st.expander(t("llm_models.phase.advanced"), expanded=False):
            new_vals["llama_cpp_n_gpu_layers"] = str(st.number_input(
                t("llm_models.phase.local_llama_cpp.n_gpu_layers"),
                value=int(settings.get("llama_cpp_n_gpu_layers", "-1") or "-1"),
                min_value=-1, max_value=999, step=1,
                key="_llmpage_main_lcpp_layers",
            ))
            new_vals["llama_cpp_n_ctx"] = str(st.number_input(
                t("llm_models.phase.local_llama_cpp.n_ctx"),
                value=int(settings.get("llama_cpp_n_ctx", "0") or "0"),
                min_value=0, max_value=131072, step=1024,
                help=t("llm_models.phase.local_llama_cpp.n_ctx_help"),
                key="_llmpage_main_lcpp_ctx",
            ))
    elif sel == "local_ollama":
        new_vals["ollama_model"] = _model_picker(
            backend=sel,
            current_value=settings.get("ollama_model", ""),
            settings=settings,
            widget_key="_llmpage_main_ollama",
            placeholder_label=t("llm_models.phase.local_ollama.model"),
        )
    elif sel == "openai":
        new_vals["openai_model"] = _model_picker(
            backend=sel,
            current_value=settings.get("openai_model", ""),
            settings=settings,
            widget_key="_llmpage_main_openai",
            placeholder_label=t("llm_models.phase.openai.model"),
        )
    elif sel == "claude":
        new_vals["anthropic_model"] = _model_picker(
            backend=sel,
            current_value=settings.get("anthropic_model", ""),
            settings=settings,
            widget_key="_llmpage_main_claude",
            placeholder_label=t("llm_models.phase.claude.model"),
        )
    elif sel == "openai_compatible":
        new_vals["compat_model"] = _model_picker(
            backend=sel,
            current_value=settings.get("compat_model", ""),
            settings=settings,
            widget_key="_llmpage_main_compat",
            placeholder_label=t("llm_models.phase.compat.model"),
        )
    elif sel in ("vllm", "vllm_offline"):
        st.caption(t("llm_models.phase.vllm.note"))

    # AI-229: traffic-light privacy bar — shows where the user's data goes for
    # the selected main backend (green = 100% local, red = internet in clear
    # text, orange = internet but redacted once that feature ships).
    _render_privacy_bar(sel, settings)

    col_save, col_test = st.columns(2)
    with col_save:
        if st.button(
            t("llm_models.main.save"),
            key="_llmpage_main_save",
            type="primary",
            width="stretch",
        ):
            svc.set_bulk(new_vals)
            # Invalidate caches that depend on the main backend.
            st.session_state["llm_backend"] = sel
            st.session_state.pop("chatbot", None)
            st.success(t("llm_models.main.saved"))
            logger.info(f"llm_models_page: main backend saved — {sel!r}")
            st.rerun()
    with col_test:
        if st.button(
            t("llm_models.main.test"),
            key="_llmpage_main_test",
            help=t("llm_models.phase.test_help"),
            width="stretch",
        ):
            # Reuse the per-phase tester, faking a "main" phase name so
            # `_run_phase_test` resolves model fields with phase-prefix
            # fallback — but here there's no phase prefix; pass the values
            # directly in a fake new_vals dict keyed by 'main_...'.
            _run_main_test(new_vals, settings)


def _render_test_result(result, elapsed: float) -> None:
    """Render an LLMTestResult inline: success / warning + hint / error."""
    if result.ok:
        st.success(t("llm_models.phase.test_ok", elapsed=f"{elapsed:.1f}"))
        return
    if result.severity == "warning" and result.hint_command:
        st.warning(t("llm_models.phase.test_arch_unsupported", error=result.message[:200]))
        st.caption(t("llm_models.phase.test_arch_hint"))
        st.code(result.hint_command, language="bash")
        return
    st.error(t("llm_models.phase.test_fail", error=result.message[:200]))


def _run_main_test(new_vals: dict, settings: dict) -> None:
    from services.llm_service import test_llm_backend

    backend = new_vals.get("llm_backend", "")
    if not backend:
        st.warning(t("llm_models.phase.test_no_backend"))
        return

    kwargs: dict = {"backend": backend}
    if backend == "local_llama_cpp":
        kwargs["model_path"] = new_vals.get("llama_cpp_model_path", "") or settings.get("llama_cpp_model_path", "")
        try:
            kwargs["n_gpu_layers"] = int(new_vals.get("llama_cpp_n_gpu_layers", "-1") or "-1")
            kwargs["n_ctx"] = int(new_vals.get("llama_cpp_n_ctx", "0") or "0")
        except (TypeError, ValueError):
            pass
    elif backend == "local_ollama":
        kwargs["base_url"] = settings.get("ollama_base_url", "http://localhost:11434")
        kwargs["model"] = new_vals.get("ollama_model", "") or settings.get("ollama_model", "")
    elif backend == "openai":
        kwargs["api_key"] = settings.get("openai_api_key", "")
        kwargs["model"] = new_vals.get("openai_model", "") or settings.get("openai_model", "")
    elif backend == "claude":
        kwargs["api_key"] = settings.get("anthropic_api_key", "")
        kwargs["model"] = new_vals.get("anthropic_model", "") or settings.get("anthropic_model", "")
    elif backend == "openai_compatible":
        kwargs["base_url"] = settings.get("compat_base_url", "")
        kwargs["api_key"] = settings.get("compat_api_key", "")
        kwargs["model"] = new_vals.get("compat_model", "") or settings.get("compat_model", "")
    elif backend in ("vllm", "vllm_offline"):
        kwargs["base_url"] = settings.get("vllm_base_url", "")
        kwargs["model"] = settings.get("vllm_model", "")
        kwargs["api_key"] = settings.get("vllm_api_key", "") or "none"

    import time as _time
    with st.spinner(t("llm_models.phase.test_running")):
        _t0 = _time.monotonic()
        result = test_llm_backend(**kwargs)
        elapsed = _time.monotonic() - _t0
    _render_test_result(result, elapsed)
    logger.info(f"llm_models_page: main backend test ok={result.ok} severity={result.severity} elapsed={elapsed:.2f}s")


def _render_account_credentials(svc: SettingsService, settings: dict) -> None:
    st.subheader(t("llm_models.credentials.title"))
    st.caption(t("llm_models.credentials.caption"))

    new_vals: dict[str, str] = {}
    with st.expander(t("llm_models.credentials.openai"), expanded=False):
        new_vals["openai_api_key"] = st.text_input(
            t("llm_models.credentials.api_key"),
            value=settings.get("openai_api_key", ""),
            type="password",
            key="_llmpage_openai_key",
        )
    with st.expander(t("llm_models.credentials.anthropic"), expanded=False):
        new_vals["anthropic_api_key"] = st.text_input(
            t("llm_models.credentials.api_key"),
            value=settings.get("anthropic_api_key", ""),
            type="password",
            key="_llmpage_anthropic_key",
        )
    with st.expander(t("llm_models.credentials.compat"), expanded=False):
        new_vals["compat_base_url"] = st.text_input(
            t("llm_models.credentials.base_url"),
            value=settings.get("compat_base_url", ""),
            key="_llmpage_compat_url",
        )
        new_vals["compat_api_key"] = st.text_input(
            t("llm_models.credentials.api_key"),
            value=settings.get("compat_api_key", ""),
            type="password",
            key="_llmpage_compat_key",
        )
    with st.expander(t("llm_models.credentials.ollama"), expanded=False):
        new_vals["ollama_base_url"] = st.text_input(
            t("llm_models.credentials.base_url"),
            value=settings.get("ollama_base_url", "http://localhost:11434"),
            key="_llmpage_ollama_url",
        )

    if st.button(
        t("llm_models.credentials.save"),
        key="_llmpage_creds_save",
        type="primary",
    ):
        svc.set_bulk({k: v for k, v in new_vals.items() if v is not None})
        st.success(t("llm_models.credentials.saved"))
        logger.info("llm_models_page: account credentials saved")


def _run_phase_test(phase: str, new_vals: dict, settings: dict) -> None:
    """Send a tiny test prompt through the phase's backend, show outcome inline.

    Reuses `services.llm_service.test_llm_backend`. Resolves account-level
    credentials (api_key, base_url) from the current settings dict because
    they aren't duplicated per phase by design.
    """
    from services.llm_service import test_llm_backend

    backend = new_vals.get(f"{phase}_llm_backend", "")
    if not backend:
        st.warning(t("llm_models.phase.test_no_backend"))
        return

    kwargs: dict = {"backend": backend}
    if backend == "local_llama_cpp":
        kwargs["model_path"] = new_vals.get(f"{phase}_llama_cpp_model_path", "") or settings.get("llama_cpp_model_path", "")
        try:
            kwargs["n_gpu_layers"] = int(new_vals.get(f"{phase}_llama_cpp_n_gpu_layers", "-1") or "-1")
            kwargs["n_ctx"] = int(new_vals.get(f"{phase}_llama_cpp_n_ctx", "0") or "0")
        except (TypeError, ValueError):
            pass
    elif backend == "local_ollama":
        kwargs["base_url"] = settings.get("ollama_base_url", "http://localhost:11434")
        kwargs["model"] = new_vals.get(f"{phase}_ollama_model", "") or settings.get("ollama_model", "")
    elif backend == "openai":
        kwargs["api_key"] = settings.get("openai_api_key", "")
        kwargs["model"] = new_vals.get(f"{phase}_openai_model", "") or settings.get("openai_model", "")
    elif backend == "claude":
        kwargs["api_key"] = settings.get("anthropic_api_key", "")
        kwargs["model"] = new_vals.get(f"{phase}_anthropic_model", "") or settings.get("anthropic_model", "")
    elif backend == "openai_compatible":
        kwargs["base_url"] = settings.get("compat_base_url", "")
        kwargs["api_key"] = settings.get("compat_api_key", "")
        kwargs["model"] = new_vals.get(f"{phase}_compat_model", "") or settings.get("compat_model", "")
    elif backend in ("vllm", "vllm_offline"):
        kwargs["base_url"] = settings.get("vllm_base_url", "")
        kwargs["model"] = settings.get("vllm_model", "")
        kwargs["api_key"] = settings.get("vllm_api_key", "") or "none"

    with st.spinner(t("llm_models.phase.test_running")):
        import time as _time
        _t0 = _time.monotonic()
        result = test_llm_backend(**kwargs)
        elapsed = _time.monotonic() - _t0
    _render_test_result(result, elapsed)
    logger.info(f"llm_models_page: phase {phase} test backend={backend} ok={result.ok} severity={result.severity} elapsed={elapsed:.2f}s")


def _render_phase_card(svc: SettingsService, settings: dict, phase: str, icon: str) -> None:
    """Render one configuration card for a single pipeline phase."""
    st.markdown(f"#### {icon} {t(f'llm_models.phase.{phase}')}")
    st.caption(t(f"llm_models.phase.{phase}.caption"))

    # "Use main backend" toggle — when on, the phase keeps no override
    # and the runtime falls back to llm_backend / cat_llm_backend.
    current_backend = settings.get(f"{phase}_llm_backend", "") or ""
    use_main = (current_backend == "")
    use_main = st.checkbox(
        t("llm_models.phase.use_main"),
        value=use_main,
        key=f"_llmpage_{phase}_usemain",
        help=t("llm_models.phase.use_main_help"),
    )

    new_vals: dict[str, str] = {}
    if use_main:
        new_vals[f"{phase}_llm_backend"] = ""
        st.info(t("llm_models.phase.using_main"))
    else:
        # Default to local_llama_cpp if nothing set — most common on first override.
        default_backend = current_backend or "local_llama_cpp"
        idx = _SUPPORTED_BACKENDS.index(default_backend) if default_backend in _SUPPORTED_BACKENDS else 0
        sel = st.selectbox(
            t("llm_models.phase.backend"),
            options=_SUPPORTED_BACKENDS,
            format_func=_backend_label,
            index=idx,
            key=f"_llmpage_{phase}_backend",
        )
        new_vals[f"{phase}_llm_backend"] = sel

        # Backend-specific fields. Account credentials live in the section above
        # and are reused — we only ask for the model identifier here.
        if sel == "local_llama_cpp":
            new_vals[f"{phase}_llama_cpp_model_path"] = _model_picker(
                backend=sel,
                current_value=settings.get(f"{phase}_llama_cpp_model_path", ""),
                settings=settings,
                widget_key=f"_llmpage_{phase}_lcpp",
                placeholder_label=t("llm_models.phase.local_llama_cpp.model_path"),
            )
            with st.expander(t("llm_models.phase.advanced"), expanded=False):
                new_vals[f"{phase}_llama_cpp_n_gpu_layers"] = str(st.number_input(
                    t("llm_models.phase.local_llama_cpp.n_gpu_layers"),
                    value=int(settings.get(f"{phase}_llama_cpp_n_gpu_layers", "-1") or "-1"),
                    min_value=-1, max_value=999, step=1,
                    key=f"_llmpage_{phase}_lcpp_layers",
                ))
                new_vals[f"{phase}_llama_cpp_n_ctx"] = str(st.number_input(
                    t("llm_models.phase.local_llama_cpp.n_ctx"),
                    value=int(settings.get(f"{phase}_llama_cpp_n_ctx", "0") or "0"),
                    min_value=0, max_value=131072, step=1024,
                    help=t("llm_models.phase.local_llama_cpp.n_ctx_help"),
                    key=f"_llmpage_{phase}_lcpp_ctx",
                ))
        elif sel == "local_ollama":
            new_vals[f"{phase}_ollama_model"] = _model_picker(
                backend=sel,
                current_value=settings.get(f"{phase}_ollama_model", ""),
                settings=settings,
                widget_key=f"_llmpage_{phase}_ollama",
                placeholder_label=t("llm_models.phase.local_ollama.model"),
            )
        elif sel == "openai":
            new_vals[f"{phase}_openai_model"] = _model_picker(
                backend=sel,
                current_value=settings.get(f"{phase}_openai_model", ""),
                settings=settings,
                widget_key=f"_llmpage_{phase}_openai",
                placeholder_label=t("llm_models.phase.openai.model"),
            )
        elif sel == "claude":
            new_vals[f"{phase}_anthropic_model"] = _model_picker(
                backend=sel,
                current_value=settings.get(f"{phase}_anthropic_model", ""),
                settings=settings,
                widget_key=f"_llmpage_{phase}_claude",
                placeholder_label=t("llm_models.phase.claude.model"),
            )
        elif sel == "openai_compatible":
            new_vals[f"{phase}_compat_model"] = _model_picker(
                backend=sel,
                current_value=settings.get(f"{phase}_compat_model", ""),
                settings=settings,
                widget_key=f"_llmpage_{phase}_compat",
                placeholder_label=t("llm_models.phase.compat.model"),
            )
        elif sel in ("vllm", "vllm_offline"):
            st.caption(t("llm_models.phase.vllm.note"))

        # Privacy banner for hosted backends — AI-55 alignment.
        if sel in ("openai", "claude", "openai_compatible"):
            st.warning(t("llm_models.phase.hosted_privacy_warning"))

    col_save, col_test = st.columns(2)
    with col_save:
        if st.button(
            t("llm_models.phase.save"),
            key=f"_llmpage_{phase}_save",
            type="primary",
            width="stretch",
        ):
            svc.set_bulk(new_vals)
            st.success(t("llm_models.phase.saved"))
            logger.info(f"llm_models_page: phase {phase} saved — backend={new_vals.get(f'{phase}_llm_backend','')!r}")
            st.rerun()
    with col_test:
        test_disabled = use_main  # no override = nothing to test for this phase
        if st.button(
            t("llm_models.phase.test"),
            key=f"_llmpage_{phase}_test",
            disabled=test_disabled,
            help=t("llm_models.phase.test_disabled_help") if test_disabled else t("llm_models.phase.test_help"),
            width="stretch",
        ):
            _run_phase_test(phase, new_vals, settings)


def render_llm_models_page(engine) -> None:
    st.header(t("llm_models.title"))
    st.caption(t("llm_models.caption"))

    svc = SettingsService(engine)
    settings = svc.get_all()

    _render_current_status(settings)
    st.divider()
    # Main backend first — it's the fallback for every phase that doesn't
    # explicitly override. Configuring it is the entry point for users
    # who just want one model everywhere; phase-specific overrides below
    # are for the power-user case.
    _render_main_backend(svc, settings)
    st.divider()
    _render_account_credentials(svc, settings)
    st.divider()

    st.subheader(t("llm_models.phases.title"))
    st.caption(t("llm_models.phases.caption"))
    cols = st.columns(2)
    for i, (phase, icon) in enumerate(_PHASES):
        with cols[i % 2]:
            with st.container(border=True):
                _render_phase_card(svc, settings, phase, icon)

    st.divider()
    st.subheader(t("llm_models.operations.title"))
    _render_stats_7d(engine)
    st.divider()
    _render_correction_benchmark(engine)
    st.divider()
    _render_calibrate_stub()
    st.divider()
    _render_download()


def _render_correction_benchmark(engine) -> None:
    """Live implicit benchmark derived from user category corrections."""
    import pandas as pd
    from services.transaction_service import TransactionService

    st.markdown(f"**{t('llm_models.benchmark.title')}**")
    st.caption(t("llm_models.benchmark.caption"))

    try:
        rows = TransactionService(engine).get_correction_benchmark()
    except Exception:
        st.caption(t("llm_models.benchmark.unavailable"))
        return

    if not rows:
        st.caption(t("llm_models.benchmark.empty"))
        return

    df = pd.DataFrame(rows)
    df.rename(columns={
        "model": t("llm_models.benchmark.col_model"),
        "total_categorized": t("llm_models.benchmark.col_total"),
        "total_corrections": t("llm_models.benchmark.col_corrections"),
        "implicit_accuracy": t("llm_models.benchmark.col_accuracy"),
        "high_conf_errors": t("llm_models.benchmark.col_hce"),
        "avg_consistency_at_error": t("llm_models.benchmark.col_consistency"),
    }, inplace=True)

    col_acc = t("llm_models.benchmark.col_accuracy")
    col_cons = t("llm_models.benchmark.col_consistency")

    import importlib.util

    styler = df.style.format(
        {
            col_acc: lambda v: f"{v:.1f}%" if v is not None else "—",
            col_cons: lambda v: f"{v:.1f}%" if v is not None else "—",
        }
    )
    # background_gradient needs matplotlib (an optional pandas dependency), and
    # pandas raises lazily at render time — inside st.dataframe — if it's absent.
    # That unhandled error would halt the Streamlit script and hide every section
    # rendered after this one (notably the model download UI). Only add the
    # gradient when matplotlib is actually installed; otherwise show a plain table.
    if importlib.util.find_spec("matplotlib") is not None:
        styler = styler.background_gradient(
            subset=[col_acc], cmap="RdYlGn", vmin=0, vmax=100
        )

    st.dataframe(styler, use_container_width=True, hide_index=True)
    st.caption(t("llm_models.benchmark.note"))


def _render_stats_7d(engine) -> None:
    """Aggregate llm_usage_log over the last 7 days, group by caller × model."""
    from sqlalchemy import text as _sql

    st.markdown(f"**{t('llm_models.stats.title')}**")
    st.caption(t("llm_models.stats.caption"))

    since = datetime.now(timezone.utc) - timedelta(days=7)
    try:
        with engine.connect() as conn:
            # SQLite lacks a built-in STDDEV — compute variance from raw
            # moments: Var(X) = E[X^2] - E[X]^2. We expose both the mean
            # and the standard deviation (sqrt(variance)) — easier to read
            # because it shares the unit of the mean (seconds, not ms²).
            rows = conn.execute(
                _sql(
                    "SELECT caller, model_id, "
                    "COUNT(*) AS n_calls, "
                    "AVG(duration_ms) AS mean_ms, "
                    "AVG(duration_ms * duration_ms) - AVG(duration_ms) * AVG(duration_ms) AS var_ms, "
                    # Per-transaction latency: divide each call's duration by
                    # its batch_size, then average. NULLIF guards against
                    # batch_size=0 and the single-shot callers (classifier,
                    # normalizer/footer) that pass batch_size=NULL — those
                    # cells will come back as NULL, surfaced as NaN in pandas
                    # and rendered blank by Streamlit (semantic: "1 call ≠
                    # N transactions, the per-tx column isn't meaningful").
                    "AVG(CAST(duration_ms AS REAL) / NULLIF(batch_size, 0)) AS ms_per_tx, "
                    "SUM(total_tokens) AS tokens "
                    "FROM llm_usage_log "
                    "WHERE timestamp >= :since "
                    "GROUP BY caller, model_id "
                    "ORDER BY n_calls DESC"
                ),
                {"since": since.isoformat()},
            ).fetchall()
    except Exception as exc:
        # Most likely cause: table missing on a freshly created DB.
        st.info(t("llm_models.stats.unavailable"))
        logger.debug(f"llm_models_page: stats query failed: {exc}")
        return

    if not rows:
        st.info(t("llm_models.stats.empty"))
        return

    df = pd.DataFrame(rows, columns=["caller", "model", "n_calls", "mean_ms", "var_ms", "ms_per_tx", "tokens"])
    df["mean_s"]   = (df["mean_ms"].astype(float) / 1000.0).round(2)
    # var_ms can be a tiny negative from FP cancellation when all samples
    # are identical — clamp at 0 before sqrt to avoid NaN.
    _var = df["var_ms"].astype(float).clip(lower=0.0)
    df["std_s"]    = ((_var ** 0.5) / 1000.0).round(2)
    df["cv_pct"]   = (df["std_s"] / df["mean_s"].replace(0, pd.NA) * 100).round(1)
    # ms_per_tx is NULL for callers without a batch_size (classifier, footer).
    # Convert to seconds; NaN stays NaN and Streamlit renders the cell blank.
    df["s_per_tx"] = pd.to_numeric(df["ms_per_tx"], errors="coerce").div(1000).round(2)
    df["n_calls"]  = df["n_calls"].astype(int)
    df["tokens"]   = df["tokens"].fillna(0).astype(int)
    st.dataframe(
        df[["caller", "model", "n_calls", "mean_s", "s_per_tx", "std_s", "cv_pct", "tokens"]],
        hide_index=True,
        width="stretch",
        column_config={
            "caller":   st.column_config.TextColumn(t("llm_models.stats.col.caller")),
            "model":    st.column_config.TextColumn(t("llm_models.stats.col.model")),
            "n_calls":  st.column_config.NumberColumn(t("llm_models.stats.col.n_calls"), format="%d"),
            "mean_s":   st.column_config.NumberColumn(t("llm_models.stats.col.mean_s"),   format="%.2f s",
                                                      help=t("llm_models.stats.col.mean_s_help")),
            "s_per_tx": st.column_config.NumberColumn(t("llm_models.stats.col.s_per_tx"), format="%.2f s",
                                                      help=t("llm_models.stats.col.s_per_tx_help")),
            "std_s":    st.column_config.NumberColumn(t("llm_models.stats.col.std_s"),    format="%.2f s",
                                                      help=t("llm_models.stats.col.std_s_help")),
            "cv_pct":   st.column_config.NumberColumn(t("llm_models.stats.col.cv_pct"),   format="%.1f %%",
                                                      help=t("llm_models.stats.col.cv_pct_help")),
            "tokens":   st.column_config.NumberColumn(t("llm_models.stats.col.tokens"),   format="%d"),
        },
    )


def _render_calibrate_stub() -> None:
    st.markdown(f"**{t('llm_models.calibrate.title')}**")
    st.caption(t("llm_models.calibrate.caption"))
    st.button(
        t("llm_models.calibrate.btn"),
        key="_llmpage_calibrate",
        disabled=True,
        help=t("llm_models.calibrate.coming_soon"),
    )


def _render_download() -> None:
    """List predefined GGUF models, exclude already-installed ones, allow download.

    Synchronous download with a live progress bar driven by the callback
    of `LlamaCppBackend.download_model`. The Streamlit script blocks
    during the transfer (matches the existing UX from the old Settings
    download widget); a deferred background-thread version with the
    `~/.spendifai/model_download.status` file used by the launcher is
    follow-up work.
    """
    from pathlib import Path

    from services.llm_service import download_gguf_model

    st.markdown(f"**{t('llm_models.download.title')}**")
    st.caption(t("llm_models.download.caption"))

    from services.llm_service import get_gguf_catalog
    all_models = get_gguf_catalog()
    to_download = [m for m in all_models if not m.downloaded]

    if not to_download:
        st.success(t("llm_models.download.all_present"))

    # Always show full catalog with download status
    with st.expander(t("llm_models.download.installed_label"), expanded=not to_download):
        for m in all_models:
            size = m.local_size_gb if m.local_size_gb is not None else m.size_gb
            icon = "✓" if m.downloaded else "⬇"
            st.markdown(f"- {icon} `{m.name}` ({size} GB)")

    if not to_download:
        return

    options = [m.name for m in to_download]
    sel = st.selectbox(
        t("llm_models.download.choose"),
        options=options,
        format_func=lambda k: next(
            f"{m.name} — {m.size_gb} GB" for m in to_download if m.name == k
        ),
        key="_llmpage_dl_choice",
    )
    sel_model = next((m for m in to_download if m.name == sel), None)
    if sel_model:
        st.caption(sel_model.description)

    if st.button(
        t("llm_models.download.btn"),
        type="primary",
        key="_llmpage_dl_btn",
    ):
        url = sel_model.url
        dest = str(Path.home() / ".spendifai" / "models" / f"{sel}.gguf")
        prog = st.progress(0.0)
        status = st.empty()

        def _cb(bytes_done: int, total_size: int) -> None:
            if total_size and total_size > 0:
                pct = min(bytes_done / total_size, 1.0)
                prog.progress(pct)
                status.text(
                    t("llm_models.download.in_progress",
                      pct=int(pct * 100),
                      done_mb=bytes_done // (1024 * 1024),
                      total_mb=total_size // (1024 * 1024))
                )

        try:
            with st.spinner(t("llm_models.download.starting")):
                final_path = download_gguf_model(url, dest, progress_callback=_cb)
            prog.progress(1.0)
            st.success(t("llm_models.download.done", path=final_path))
            logger.info(f"llm_models_page: downloaded {sel} to {final_path}")
            st.rerun()
        except Exception as exc:
            st.error(t("llm_models.download.error", error=str(exc)[:200]))
            logger.warning(f"llm_models_page: download {sel} failed: {exc}")
