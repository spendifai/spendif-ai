"""Settings page — persistent user preferences (locale, language, LLM)."""
from __future__ import annotations

import json

import streamlit as st

from services.settings_service import SettingsService
from support.logging import setup_logging
from ui.i18n import t

logger = setup_logging()


def _key_for(options: dict, value: str) -> str:
    """Return the display label whose value matches, or first key as fallback."""
    for label, v in options.items():
        if v == value:
            return label
    return next(iter(options))


def _do_ollama_pull(base_url: str, model: str) -> None:
    """Pull (download/update) an Ollama model with streaming progress."""
    import requests

    base_url = base_url.rstrip("/")
    try:
        with st.spinner(t("settings.download_model_spinner", model=model)):
            resp = requests.post(
                f"{base_url}/api/pull",
                json={"name": model, "stream": True},
                stream=True,
                timeout=600,
            )
            resp.raise_for_status()

            progress_bar = st.progress(0.0)
            status_text = st.empty()

            for line in resp.iter_lines():
                if not line:
                    continue
                import json as _json
                data = _json.loads(line)
                status = data.get("status", "")
                total = data.get("total", 0)
                completed = data.get("completed", 0)

                if total > 0:
                    pct = completed / total
                    progress_bar.progress(min(pct, 1.0))
                    size_mb = total / (1024 * 1024)
                    done_mb = completed / (1024 * 1024)
                    status_text.caption(t("settings.download_status", status=status, done=f"{done_mb:.0f}", total=f"{size_mb:.0f}"))
                else:
                    status_text.caption(status)

            progress_bar.progress(1.0)
            st.success(t("settings.ollama.model_ready", model=model))
    except requests.ConnectionError:
        st.error(t("settings.ollama.connection_error", url=base_url))
    except requests.HTTPError as exc:
        st.error(t("settings.ollama.http_error", error=exc))
    except Exception as exc:
        st.error(t("settings.ollama.pull_error", error=exc))


def _autodetect_ctx_llama() -> None:
    """on_change callback: read GGUF context length and update session state."""
    from services.llm_service import detect_llama_cpp_context
    path = st.session_state.get("_wgt_llama_path", "")
    ctx = detect_llama_cpp_context(path)
    if ctx:
        st.session_state["_wgt_llama_n_ctx"] = ctx


def _autodetect_ctx_ollama() -> None:
    """on_change callback: query Ollama /api/show for context length."""
    from services.llm_service import detect_ollama_context
    model   = st.session_state.get("_wgt_ollama_model", "")
    base_url = st.session_state.get("_wgt_ollama_url", "http://localhost:11434")
    ctx = detect_ollama_context(model, base_url)
    st.session_state["_ollama_ctx_detected"] = ctx


def _autodetect_ctx_openai() -> None:
    """on_change callback: lookup known context window for OpenAI models."""
    from services.llm_service import get_known_context_window
    model = st.session_state.get("_wgt_openai_model", "")
    st.session_state["_openai_ctx_detected"] = get_known_context_window(model)


def _autodetect_ctx_claude() -> None:
    """on_change callback: lookup known context window for Claude models."""
    from services.llm_service import get_known_context_window
    model = st.session_state.get("_wgt_claude_model", "")
    st.session_state["_claude_ctx_detected"] = get_known_context_window(model)


def _autodetect_ctx_vllm() -> None:
    """on_change callback: query vLLM /v1/models for context length."""
    from services.llm_service import detect_vllm_context
    base_url = st.session_state.get("_wgt_vllm_url", "http://localhost:8000/v1")
    model    = st.session_state.get("_wgt_vllm_model", "")
    ctx = detect_vllm_context(base_url, model)
    st.session_state["_vllm_ctx_detected"] = ctx


def _ctx_caption(ctx: int | None) -> str:
    """Format a detected context length as a Streamlit caption string."""
    if ctx:
        return f"📐 contesto nativo: **{ctx:,}** token"
    return ""


def _do_llm_test(
    backend: str,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    **extra_kwargs,
) -> None:
    """Send a minimal test prompt to the configured LLM backend."""
    from services.llm_service import create_backend, LLMValidationError

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
        elif backend == "openai_compatible":
            kwargs["base_url"] = base_url
            kwargs["api_key"] = api_key
            kwargs["model"] = model

        llm = create_backend(backend, **kwargs)

        with st.spinner(t("settings.test_llm_spinner")):
            result = llm.complete_structured(
                system_prompt="Rispondi in JSON.",
                user_prompt='Classifica questa transazione: "PAGAMENTO POS FARMACIA". Rispondi con category e confidence.',
                json_schema={
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["category", "confidence"],
                },
            )
            cat = result.get("category", "?")
            conf = result.get("confidence", "?")
            st.success(t("settings.test_llm_ok", cat=cat, conf=conf))
            ctx = llm.get_context_info()
            if ctx:
                n_cfg = ctx.get("n_ctx")
                n_max = ctx.get("n_ctx_train")
                parts = []
                if n_cfg:
                    parts.append(t("settings.test_llm_ctx_configured", n=f"{n_cfg:,}"))
                if n_max:
                    parts.append(t("settings.test_llm_ctx_max", n=f"{n_max:,}"))
                if parts:
                    st.info("📐 " + " · ".join(parts))

    except LLMValidationError as exc:
        st.error(t("settings.test_llm_validation_error", error=exc))
    except Exception as exc:
        error_msg = str(exc)
        if "Connection" in error_msg or "refused" in error_msg:
            st.error(t("settings.test_llm_connection_error", error=error_msg))
        elif "401" in error_msg or "auth" in error_msg.lower():
            st.error(t("settings.test_llm_auth_error", error=error_msg))
        else:
            st.error(t("settings.test_llm_generic_error", error=error_msg))


def render_settings_page(engine):
    # ── Build option dicts inside function so t() works at runtime ─────────
    _DATE_FORMAT_OPTIONS = {
        t("settings.date_fmt.dmy"): "%d/%m/%Y",
        t("settings.date_fmt.iso"): "%Y-%m-%d",
        t("settings.date_fmt.mdy"): "%m/%d/%Y",
    }

    _DECIMAL_SEP_OPTIONS = {
        t("settings.decimal_sep.comma"): ",",
        t("settings.decimal_sep.dot"): ".",
    }

    _THOUSANDS_SEP_OPTIONS = {
        t("settings.thousands_sep.dot"): ".",
        t("settings.thousands_sep.comma"): ",",
        t("settings.thousands_sep.space"): " ",
        t("settings.thousands_sep.none"): "",
    }

    _LANGUAGE_OPTIONS = {
        t("settings.lang.it"): "it",
        t("settings.lang.en"): "en",
        t("settings.lang.fr"): "fr",
        t("settings.lang.de"): "de",
    }

    _ACCOUNT_TYPES = {
        t("settings.account_type.bank_account"): "bank_account",
        t("settings.account_type.credit_card"): "credit_card",
        t("settings.account_type.debit_card"): "debit_card",
        t("settings.account_type.prepaid_card"): "prepaid_card",
        t("settings.account_type.savings_account"): "savings_account",
        t("settings.account_type.cash"): "cash",
    }

    _ACCOUNT_TYPE_LABELS = {v: k for k, v in _ACCOUNT_TYPES.items()}

    _GIROCONTO_OPTIONS = {
        t("settings.giroconto.neutral"): "neutral",
        t("settings.giroconto.exclude"): "exclude",
    }

    _BACKEND_OPTIONS = {
        t("settings.backend.llama_cpp"): "local_llama_cpp",
        t("settings.backend.ollama"): "local_ollama",
        t("settings.backend.openai"): "openai",
        t("settings.backend.claude"): "claude",
        t("settings.backend.openai_compatible"): "openai_compatible",
    }

    st.header(t("settings.title"))

    cfg_svc = SettingsService(engine)
    settings = cfg_svc.get_all()

    # Sync session_state immediately so other pages (upload, ledger) see current values
    st.session_state.setdefault("giroconto_mode", settings.get("giroconto_mode", "neutral"))

    # ── Formato visualizzazione ────────────────────────────────────────────────
    st.subheader(t("settings.display_format"))

    date_label = st.selectbox(
        t("settings.date_format"),
        list(_DATE_FORMAT_OPTIONS.keys()),
        index=list(_DATE_FORMAT_OPTIONS.keys()).index(
            _key_for(_DATE_FORMAT_OPTIONS, settings.get("date_display_format", "%d/%m/%Y"))
        ),
    )

    col1, col2 = st.columns(2)
    with col1:
        dec_label = st.selectbox(
            t("settings.decimal_sep"),
            list(_DECIMAL_SEP_OPTIONS.keys()),
            index=list(_DECIMAL_SEP_OPTIONS.keys()).index(
                _key_for(_DECIMAL_SEP_OPTIONS, settings.get("amount_decimal_sep", ","))
            ),
        )
    with col2:
        thou_label = st.selectbox(
            t("settings.thousands_sep"),
            list(_THOUSANDS_SEP_OPTIONS.keys()),
            index=list(_THOUSANDS_SEP_OPTIONS.keys()).index(
                _key_for(_THOUSANDS_SEP_OPTIONS, settings.get("amount_thousands_sep", "."))
            ),
        )

    # Preview
    from support.formatting import format_amount_display, format_date_display
    preview_date = format_date_display("2025-12-31", _DATE_FORMAT_OPTIONS[date_label])
    preview_amount = format_amount_display(
        1234.56,
        decimal_sep=_DECIMAL_SEP_OPTIONS[dec_label],
        thousands_sep=_THOUSANDS_SEP_OPTIONS[thou_label],
    )
    st.info(t("settings.preview", date=preview_date, amount=preview_amount))

    st.divider()

    # ── Lingua descrizioni ─────────────────────────────────────────────────────
    st.subheader(t("settings.desc_language"))
    st.caption(t("settings.desc_language_caption"))

    lang_label = st.selectbox(
        t("settings.desc_language_label"),
        list(_LANGUAGE_OPTIONS.keys()),
        index=list(_LANGUAGE_OPTIONS.keys()).index(
            _key_for(_LANGUAGE_OPTIONS, settings.get("description_language", "it"))
        ),
    )

    st.divider()

    # ── Lingua interfaccia (i18n) ──────────────────────────────────────────────
    st.subheader(t("settings.ui_language"))
    st.caption(t("settings.ui_language_caption"))
    from ui.i18n import available_languages
    _ui_langs = available_languages()  # [(code, label), ...]
    _ui_lang_labels = [label for _, label in _ui_langs]
    _ui_lang_codes = [code for code, _ in _ui_langs]
    _current_ui_lang = settings.get("ui_language", "it")
    _ui_lang_idx = _ui_lang_codes.index(_current_ui_lang) if _current_ui_lang in _ui_lang_codes else 0
    ui_lang_label = st.selectbox(
        t("settings.ui_language_label"),
        _ui_lang_labels,
        index=_ui_lang_idx,
    )
    ui_language = _ui_lang_codes[_ui_lang_labels.index(ui_lang_label)]

    st.divider()

    # ── Paese ──────────────────────────────────────────────────────────────────
    st.subheader(t("settings.country"))
    st.caption(t("settings.country_caption"))
    # Country names are now resolved per active UI language via the onboarding
    # helpers; the old _COUNTRIES / _COUNTRY_LABELS / _COUNTRY_BY_NAME constants
    # were removed in the i18n country refactor (AI-16).
    from ui.onboarding_page import (
        _COUNTRY_CODES,
        _country_label,
        _code_from_label,
        _sorted_country_labels,
    )
    _none_label = t("settings.country_none")
    _country_labels = _sorted_country_labels()
    _country_options = [_none_label] + _country_labels
    _current_country = settings.get("country", "")
    if _current_country in _COUNTRY_CODES:
        _current_label = _country_label(_current_country)
        _country_idx = (
            _country_options.index(_current_label)
            if _current_label in _country_options else 0
        )
    else:
        _country_idx = 0
    country_sel = st.selectbox(
        t("settings.country_label"),
        _country_options,
        index=_country_idx,
        label_visibility="collapsed",
    )
    country_code = "" if country_sel == _none_label else (_code_from_label(country_sel) or "")

    st.divider()

    # ── Modalità Giroconti ─────────────────────────────────────────────────────
    st.subheader(t("settings.giroconto_mode_title"))
    st.caption(t("settings.giroconto_mode_caption"))

    giroconto_label = st.radio(
        t("settings.giroconto_label"),
        list(_GIROCONTO_OPTIONS.keys()),
        index=list(_GIROCONTO_OPTIONS.keys()).index(
            _key_for(_GIROCONTO_OPTIONS, settings.get("giroconto_mode", "neutral"))
        ),
        label_visibility="collapsed",
        horizontal=True,
    )

    st.divider()

    # ── Titolari del conto ─────────────────────────────────────────────────────
    st.subheader(t("settings.owners_title"))
    st.caption(t("settings.owners_caption"))
    owner_names_raw = st.text_input(
        t("settings.owners_label"),
        value=settings.get("owner_names", ""),
        placeholder=t("settings.owners_placeholder"),
    )

    _owner_list = [n.strip() for n in owner_names_raw.split(",") if n.strip()]
    use_owner_giroconto = st.toggle(
        t("settings.use_owners_giroconto"),
        value=settings.get("use_owner_names_giroconto", "false").lower() == "true",
        disabled=not _owner_list,
        help=t("settings.use_owners_giroconto_help"),
    )

    st.divider()

    # ── Contesti di vita ───────────────────────────────────────────────────────
    st.subheader(t("settings.contexts_title"))
    st.caption(t("settings.contexts_caption"))

    try:
        _ctx_list: list[str] = json.loads(settings.get("contexts", '["Quotidianità", "Lavoro", "Vacanza"]'))
    except Exception:
        _ctx_list = ["Quotidianità", "Lavoro", "Vacanza"]

    if "settings_contexts" not in st.session_state:
        st.session_state["settings_contexts"] = list(_ctx_list)

    ctx_to_remove = None
    for i, ctx in enumerate(st.session_state["settings_contexts"]):
        cc1, cc2 = st.columns([5, 1])
        with cc1:
            new_val = st.text_input(
                t("settings.context_n", n=i + 1), value=ctx, key=f"ctx_val_{i}", label_visibility="collapsed"
            )
            st.session_state["settings_contexts"][i] = new_val.strip()
        with cc2:
            if st.button("🗑️", key=f"ctx_del_{i}", help=t("settings.remove_context")):
                ctx_to_remove = i

    if ctx_to_remove is not None:
        st.session_state["settings_contexts"].pop(ctx_to_remove)
        st.rerun()

    with st.form("new_ctx_form", clear_on_submit=True):
        nc1, nc2 = st.columns([4, 1])
        new_ctx = nc1.text_input(t("settings.new_context"), placeholder=t("settings.new_context_placeholder"), label_visibility="collapsed")
        if nc2.form_submit_button(t("settings.add_context")):
            val = new_ctx.strip()
            if val and val not in st.session_state["settings_contexts"]:
                st.session_state["settings_contexts"].append(val)
                st.rerun()

    st.divider()

    # ── Import ─────────────────────────────────────────────────────────────────
    st.subheader(t("settings.import_title"))

    force_schema_import = st.toggle(
        t("settings.force_schema"),
        value=settings.get("force_schema_import", "false").lower() == "true",
        help=t("settings.force_schema_help"),
    )
    if force_schema_import:
        st.caption(t("settings.force_schema_active"))

    import_test_mode = st.toggle(
        t("settings.test_mode"),
        value=settings.get("import_test_mode", "false").lower() == "true",
        help=t("settings.test_mode_help"),
    )
    if import_test_mode:
        st.caption(t("settings.test_mode_active"))

    max_tx_amount = st.number_input(
        t("settings.max_tx_amount"),
        min_value=1_000,
        max_value=100_000_000,
        value=int(settings.get("max_transaction_amount", "1000000")),
        step=10_000,
        help=t("settings.max_tx_amount_help"),
    )

    st.divider()

    # ── Conti bancari ──────────────────────────────────────────────────────────
    st.subheader(t("settings.accounts_title"))
    st.caption(t("settings.accounts_caption"))

    _accounts = cfg_svc.get_accounts()

    with st.form("new_account_form", clear_on_submit=True):
        col_name, col_bank, col_type, col_btn = st.columns([2, 2, 2, 1])
        new_acc_name = col_name.text_input(t("settings.account_name"), placeholder="Conto corrente POPSO")
        new_acc_bank = col_bank.text_input(t("settings.account_bank"), placeholder="Banca Popolare di Sondrio")
        new_acc_type_label = col_type.selectbox(
            t("settings.account_type"), list(_ACCOUNT_TYPES.keys()), index=0,
        )
        if col_btn.form_submit_button(t("settings.add_account"), width="stretch"):
            if new_acc_name.strip():
                try:
                    cfg_svc.create_account(
                        new_acc_name, new_acc_bank or "",
                        account_type=_ACCOUNT_TYPES[new_acc_type_label],
                    )
                    st.success(t("settings.account_added", name=new_acc_name))
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning(t("settings.account_name_empty"))

    if _accounts:
        for acc in _accounts:
            c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 1, 1])
            c1.markdown(f"**{acc.name}**")
            c2.caption(acc.bank_name or "—")
            c3.caption(_ACCOUNT_TYPE_LABELS.get(acc.account_type or "", acc.account_type or "—"))
            edit_key = f"edit_acc_{acc.id}"
            if c4.button("✏️", key=edit_key, help=t("settings.edit_account")):
                st.session_state[f"_editing_acc"] = acc.id
            if c5.button("🗑️", key=f"del_acc_{acc.id}", help=t("settings.delete_account")):
                cfg_svc.delete_account(acc.id)
                st.rerun()

            if st.session_state.get("_editing_acc") == acc.id:
                with st.container(border=True):
                    ec1, ec2, ec3 = st.columns(3)
                    edited_name = ec1.text_input(
                        t("settings.account_name"), value=acc.name, key=f"ren_name_{acc.id}"
                    )
                    edited_bank = ec2.text_input(
                        t("settings.account_bank"), value=acc.bank_name or "", key=f"ren_bank_{acc.id}"
                    )
                    _current_type = acc.account_type or "bank_account"
                    _type_labels = list(_ACCOUNT_TYPES.keys())
                    _type_values = list(_ACCOUNT_TYPES.values())
                    _type_idx = _type_values.index(_current_type) if _current_type in _type_values else 0
                    edited_type_label = ec3.selectbox(
                        t("settings.account_type"), _type_labels, index=_type_idx, key=f"ren_type_{acc.id}"
                    )
                    bc1, bc2 = st.columns(2)
                    if bc1.button(t("common.save"), key=f"save_acc_{acc.id}", type="primary"):
                        if not edited_name.strip():
                            st.error(t("settings.account_name_empty"))
                        else:
                            try:
                                n = cfg_svc.rename_account(
                                    acc.id, edited_name, edited_bank or None,
                                    new_account_type=_ACCOUNT_TYPES[edited_type_label],
                                )
                                st.session_state.pop("_editing_acc", None)
                                st.success(t("settings.account_renamed", n=n))
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                    if bc2.button(t("common.cancel"), key=f"cancel_acc_{acc.id}"):
                        st.session_state.pop("_editing_acc", None)
                        st.rerun()
    else:
        st.info(t("settings.no_accounts"))

    st.divider()

    # ── Database info ─────────────────────────────────────────────────────────
    st.subheader(t("settings.db_info.title"))
    st.caption(t("settings.db_info.caption"))
    _db_info = cfg_svc.get_db_info()
    _info_col_a, _info_col_b = st.columns([1, 2], vertical_alignment="center")
    with _info_col_a:
        st.markdown(f"**{t('settings.db_info.type')}**")
    with _info_col_b:
        st.markdown(_db_info["display_label"])
    _info_col_a, _info_col_b = st.columns([1, 2], vertical_alignment="center")
    with _info_col_a:
        _location_label = (
            t("settings.db_info.path") if _db_info["kind"] == "file" else t("settings.db_info.address")
        )
        st.markdown(f"**{_location_label}**")
    with _info_col_b:
        # Use code formatting so long paths / connection strings stay
        # readable on narrow viewports and the user can click-copy.
        st.code(_db_info["location"], language=None)
    if _db_info["kind"] == "file":
        _info_col_a, _info_col_b = st.columns([1, 2], vertical_alignment="center")
        with _info_col_a:
            st.markdown(f"**{t('settings.db_info.size')}**")
        with _info_col_b:
            if _db_info.get("file_exists"):
                st.markdown(f"{_db_info['file_size_mb']:.1f} MB")
            else:
                st.markdown(f"⚠️ {t('settings.db_info.file_missing')}")
    elif _db_info["kind"] == "server" and _db_info.get("user"):
        _info_col_a, _info_col_b = st.columns([1, 2], vertical_alignment="center")
        with _info_col_a:
            st.markdown(f"**{t('settings.db_info.user')}**")
        with _info_col_b:
            st.markdown(_db_info["user"])

    st.divider()

    # ── Schema cache ──────────────────────────────────────────────────────────
    st.subheader(t("settings.schema_cache_title"))
    st.caption(t("settings.schema_cache_caption"))
    if st.button(t("settings.clear_schemas_btn"), help=t("settings.clear_schemas_help")):
        n = cfg_svc.delete_all_schemas()
        st.success(t("settings.schemas_cleared", n=n))
        st.rerun()

    st.divider()

    # ── Configurazione LLM ─────────────────────────────────────────────────────
    # Moved to the dedicated 🤖 LLM Models page (AI-96). This stub keeps
    # the Settings page focused on application behaviour (locale, accounts,
    # taxonomy reset) and lets the LLM page own the per-phase backend grid,
    # account credentials and operations (download/test/calibrate/stats).
    st.subheader(t("settings.llm_config_moved.title"))
    st.info(t("settings.llm_config_moved.body"))

    st.divider()

    # ── Profili rapidi (sezione nascosta per power user) ─────────────────────
    with st.expander(t("settings.power_user_title"), expanded=False):
        st.caption(t("settings.power_user_caption"))
        if st.button(t("settings.power_user_btn"), key="apply_nerd_profile"):
            # Force schema import — no review popup
            force_schema_import = True
            # Test mode off — process all rows
            import_test_mode = False
            # Max transaction amount — high ceiling
            max_tx_amount = 10_000_000
            st.success(t("settings.power_user_applied"))

    st.divider()

    # ── Salva ──────────────────────────────────────────────────────────────────
    if st.button(t("settings.save_btn"), type="primary"):
        _ctx_clean = [c for c in st.session_state.get("settings_contexts", _ctx_list) if c]
        # LLM keys are no longer written by this page — they live in the
        # dedicated 🤖 LLM Models page (AI-96). The DB rows for llm_backend,
        # llama_cpp_*, ollama_*, openai_*, anthropic_*, compat_*, cat_*
        # and the new per-phase classifier_*/cleaner_*/categorizer_*/footer_*
        # are managed there. This save only covers application behaviour.
        cfg_svc.set_bulk({
            "date_display_format":    _DATE_FORMAT_OPTIONS[date_label],
            "amount_decimal_sep":     _DECIMAL_SEP_OPTIONS[dec_label],
            "amount_thousands_sep":   _THOUSANDS_SEP_OPTIONS[thou_label],
            "description_language":   _LANGUAGE_OPTIONS[lang_label],
            "ui_language":            ui_language,
            "country":                country_code,
            "giroconto_mode":         _GIROCONTO_OPTIONS[giroconto_label],
            "owner_names":            owner_names_raw.strip(),
            "use_owner_names_giroconto": "true" if use_owner_giroconto else "false",
            "import_test_mode":       "true" if import_test_mode else "false",
            "force_schema_import":   "true" if force_schema_import else "false",
            "max_transaction_amount": str(int(max_tx_amount)),
            "contexts":               json.dumps(_ctx_clean, ensure_ascii=False),
        })

        st.session_state["giroconto_mode"] = _GIROCONTO_OPTIONS[giroconto_label]
        st.session_state.pop("settings_contexts", None)
        st.success(t("settings.saved"))
        logger.info("settings_page: saved app-behaviour settings (LLM config is owned by the LLM Models page)")
        st.rerun()

    # ── Reset tassonomia ───────────────────────────────────────────────────────
    st.divider()
    with st.expander(t("settings.reset_taxonomy_title"), expanded=False):
        st.warning(t("settings.reset_taxonomy_warning"))
        lang_options = cfg_svc.get_default_taxonomy_languages()   # [(code, label)]
        lang_labels  = [label for _, label in lang_options]
        lang_codes   = [code  for code, _ in lang_options]
        current_lang = settings.get("description_language", "it")
        default_idx  = lang_codes.index(current_lang) if current_lang in lang_codes else 0
        reset_lang_label = st.selectbox(
            t("settings.reset_taxonomy_lang"),
            options=lang_labels,
            index=default_idx,
            key="settings_reset_tax_lang",
        )
        reset_lang_code = lang_codes[lang_labels.index(reset_lang_label)]
        confirm_reset = st.checkbox(
            t("settings.reset_taxonomy_confirm"),
            key="settings_reset_tax_confirm",
        )
        if st.button(
            t("settings.reset_taxonomy_btn"),
            type="secondary",
            disabled=not confirm_reset,
            key="settings_reset_tax_btn",
        ):
            n = cfg_svc.apply_default_taxonomy(reset_lang_code)
            st.success(t("settings.reset_taxonomy_applied", lang=reset_lang_label, n=n))
            logger.info(f"settings_page: reset taxonomy lang={reset_lang_code!r} categories={n}")
            st.rerun()
