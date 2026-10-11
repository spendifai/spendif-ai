"""Dev-only pipeline Debugger page (AI-193).

Trace the full import pipeline (header → classify → footer → extraction →
cleaner → categorizer) on a small sample of a file, IN CLEAR and WITHOUT
writing anything to the DB. Built to diagnose pipeline bugs such as AI-149
(Satispay/Amex sign inversion) without git revert or temporary prints.

Gated behind the SPENDIFAI_DEV_MODE=1 environment variable (see app.py /
sidebar.py): it never appears in production. Labels are hardcoded Italian on
purpose — this is an internal developer tool, not a user-facing feature.
"""
from __future__ import annotations

import json
import os
import re

import streamlit as st

from services.import_service import ImportService

# Ordered for a stable selectbox (frozenset VALID_ACCOUNT_TYPES has no order).
_ACCOUNT_TYPES = [
    "bank_account", "credit_card", "debit_card",
    "prepaid_card", "savings_account", "cash",
]

# Broad emoji / pictograph ranges — used to flag descriptions the cleaner
# should have stripped (Satispay 'Tipo' column carries 🏬/🛡️/🏦).
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F300-\U0001F9FF←-⇿⬀-⯿️]"
)


def _is_dev_mode() -> bool:
    return os.getenv("SPENDIFAI_DEV_MODE") == "1"


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _sign_flipped(raw_amount, amount) -> bool:
    r, a = _to_float(raw_amount), _to_float(amount)
    if r is None or a is None or r == 0 or a == 0:
        return False
    return (r > 0) != (a > 0)


def render_debugger_page(engine) -> None:
    if not _is_dev_mode():
        st.warning("Pagina disponibile solo in modalità sviluppatore (SPENDIFAI_DEV_MODE=1).")
        st.stop()

    st.header("🔬 Debugger pipeline")
    st.caption(
        "Trace step-by-step di tutte le fasi fino alla categorizzazione inclusa. "
        "**Nessuna scrittura sul DB** — il file viene solo analizzato."
    )

    svc = ImportService(engine)

    # ── Input controls ────────────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "File da tracciare (CSV / XLSX)", type=["csv", "xlsx", "xls"], key="dbg_uploader"
    )
    col1, col2 = st.columns(2)
    with col1:
        account_choice = st.selectbox(
            "Simula tipo di documento (solo dev)",
            ["(come decide il sistema)"] + _ACCOUNT_TYPES,
            help="Non e' una scelta utente e non esiste nell'import normale: li' il tipo "
            "di documento lo legge il sistema dal file, e il tipo di conto dichiarato non "
            "entra piu' nella decisione del segno. Qui lo puoi forzare SOLO per diagnostica, "
            "per vedere cosa produrrebbe la pipeline leggendo lo stesso file come tipo X e "
            "isolare dove i segni si invertono. Lascia il default per osservare il "
            "comportamento reale.",
        )
    with col2:
        n_sample = st.number_input(
            "Campione (numero record)", min_value=1, max_value=500, value=20, step=1
        )

    run = st.button("▶ Esegui trace", type="primary", disabled=uploaded is None)

    if run and uploaded is not None:
        doc_type_override = None if account_choice.startswith("(come decide") else account_choice
        raw_bytes = uploaded.getvalue()
        trace: list = []
        config = svc.build_config(test_mode=True)
        config.test_mode_rows = int(n_sample)
        with st.spinner("Esecuzione pipeline (nessun salvataggio)…"):
            try:
                result = svc.process_file_single(
                    raw_bytes=raw_bytes,
                    filename=uploaded.name,
                    config=config,
                    doc_type_override=doc_type_override,
                    existing_tx_ids_checker=lambda ids: set(),  # keep every sampled row
                    llm_trace=trace,
                )
            except Exception as exc:  # noqa: BLE001 — surface anything in clear
                st.session_state.pop("dbg_result", None)
                st.error(f"Pipeline interrotta da un'eccezione: {exc!r}")
                st.exception(exc)
                return
        st.session_state["dbg_result"] = result
        st.session_state["dbg_trace"] = trace
        st.session_state["dbg_meta"] = {
            "filename": uploaded.name,
            "doc_type_override": doc_type_override,
            "confidence_threshold": config.confidence_threshold,
            "sample_n": int(n_sample),
        }

    result = st.session_state.get("dbg_result")
    if result is None:
        st.info("Carica un file e premi **Esegui trace** per vedere il flusso completo.")
        return

    trace = st.session_state.get("dbg_trace", [])
    meta = st.session_state.get("dbg_meta", {})
    schema = result.doc_schema
    threshold = meta.get("confidence_threshold", 0.80)

    _render_phase_summary(result, schema, meta)
    _render_transaction_trace(result, schema, threshold)
    _render_llm_io(trace)
    _render_export(result, schema, trace, meta)


# ── Phase summary (global, in pipeline order) ───────────────────────────────────

def _render_phase_summary(result, schema, meta) -> None:
    st.subheader("Passaggi pipeline (in ordine di esecuzione)")
    st.caption(f"Timings per fase (ms): {result.phase_durations_ms}")

    # PASSO 1 — Caricamento & header detection
    with st.expander("PASSO 1 — Caricamento & header detection", expanded=True):
        st.json({
            "flow_usato": result.flow_used,
            "batch_sha256": result.batch_sha256,
            "campione_richiesto": meta.get("sample_n"),
            "righe_totali_file": result.total_file_rows,
            "righe_header_scartate": result.header_rows_skipped,
            "available_columns": result.available_columns,
            "sheet": getattr(schema, "sheet_name", None) if schema else None,
            "delimiter": getattr(schema, "delimiter", None) if schema else None,
            "encoding": getattr(schema, "encoding", None) if schema else None,
            "has_borders": getattr(schema, "has_borders", None) if schema else None,
            "skip_rows": getattr(schema, "skip_rows", None) if schema else None,
        })

    # PASSO 2 — Classify / Phase 0
    with st.expander("PASSO 2 — Classify (Phase 0 / schema dedotto)", expanded=True):
        st.caption("Prompt e risposta grezzi del classifier: sezione «I/O LLM», fase `classify`.")
        if schema is None:
            st.warning(f"Nessuno schema. needs_schema_review={result.needs_schema_review}")
        else:
            sd = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
            st.json({
                "needs_schema_review": result.needs_schema_review,
                "account_type_forzato_dev": meta.get("doc_type_override"),
                "doc_type": str(sd.get("doc_type")),
                "sign_convention": str(sd.get("sign_convention")),
                "invert_sign": sd.get("invert_sign"),
                "confidence": str(sd.get("confidence")),
                "confidence_score": sd.get("confidence_score"),
                "normalization_case_id": sd.get("normalization_case_id"),
                "date_col": sd.get("date_col"),
                "date_accounting_col": sd.get("date_accounting_col"),
                "amount_col": sd.get("amount_col"),
                "debit_col": sd.get("debit_col"),
                "credit_col": sd.get("credit_col"),
                "description_col": sd.get("description_col"),
                "description_cols": sd.get("description_cols"),
                "currency_col": sd.get("currency_col"),
                "default_currency": sd.get("default_currency"),
                "positive_ratio": sd.get("positive_ratio"),
                "negative_ratio": sd.get("negative_ratio"),
                "internal_transfer_patterns": sd.get("internal_transfer_patterns"),
                "footer_patterns": sd.get("footer_patterns"),
                "semantic_evidence": sd.get("semantic_evidence"),
            })

    # PASSO 3 — Footer stripping (3 fasi)
    with st.expander("PASSO 3 — Footer stripping (deterministico → pattern → LLM → IQR)"):
        st.caption("Le 3 fasi di footer-strip non espongono conteggi singoli in ImportResult; "
                   "le eventuali chiamate LLM sono nella sezione «I/O LLM», fase `footer`. "
                   "I footer_patterns dedotti sono nel PASSO 2.")
        st.write(f"Righe unite (merge intra-file): **{result.merged_count}**")

    # PASSO 4 — Estrazione / normalizzazione + sign convention
    with st.expander("PASSO 4 — Estrazione / normalizzazione (sign_convention + invert_sign)"):
        st.write(f"Transazioni estratte: **{len(result.transactions)}**")
        st.write(f"Righe unite (merge): **{result.merged_count}**")
        if schema is not None:
            sd = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
            st.caption(f"Applicati: sign_convention=`{sd.get('sign_convention')}`, "
                       f"invert_sign=`{sd.get('invert_sign')}` → effetto sui segni visibile "
                       "nella tabella per-transazione (colonna `→ amount`).")
        if result.skipped_rows:
            rows = [{"row_index": sr.row_index, "reason": sr.reason, **sr.raw_values}
                    for sr in result.skipped_rows]
            st.write(f"Righe scartate in normalizzazione: **{len(rows)}**")
            st.dataframe(rows, width="stretch")
        else:
            st.caption("Nessuna riga scartata in normalizzazione.")

    # PASSO 5 — Dedup (pre-LLM)
    with st.expander("PASSO 5 — Dedup transazioni già importate (pre-LLM)"):
        st.write(f"Già presenti su DB (saltate prima dell'LLM): **{result.skipped_count}**")
        st.write(f"File interamente duplicato: **{result.skipped_duplicate}**")
        st.caption("Nel debugger il checker duplicati è forzato a vuoto → "
                   "nessuna riga del campione viene pre-scartata.")

    # PASSO 6 — Cleaning descrizioni
    with st.expander("PASSO 6 — Cleaning descrizioni (estrazione controparte)"):
        st.caption("Prima→dopo per ogni tx nella tabella (colonne `raw_description` → `→ description`). "
                   "Prompt/risposta grezzi: sezione «I/O LLM», fase `cleaner`.")

    # PASSO 7 — Giroconti / transfer detection
    with st.expander("PASSO 7 — Rilevazione giroconti & transfer links"):
        st.write(f"Giroconti rilevati (owner-name + keyword pass): **{result.internal_transfer_count}**")
        if result.transfer_links:
            st.write(f"Transfer links: **{len(result.transfer_links)}**")
            st.dataframe(result.transfer_links, width="stretch")
        else:
            st.caption("Nessun transfer link.")

    # PASSO 8 — Riconciliazione carta (RF-03)
    with st.expander("PASSO 8 — Riconciliazione settlement carta (RF-03)"):
        if result.reconciliations:
            st.write(f"Riconciliazioni: **{len(result.reconciliations)}**")
            st.dataframe(result.reconciliations, width="stretch")
        else:
            st.caption("Nessuna riconciliazione carta.")

    # PASSO 9 — Categorizzazione
    with st.expander("PASSO 9 — Categorizzazione"):
        st.caption("Esito per tx nella tabella (category/subcategory/confidence/source/model). "
                   "Prompt/risposta grezzi: sezione «I/O LLM», fase `categorizer`.")

    # Errori pipeline
    if result.errors:
        st.error("Errori riportati dalla pipeline:")
        st.json(result.errors)


# ── Per-transaction trace (cleaner + categorizer in clear) ──────────────────────

def _render_transaction_trace(result, schema, threshold) -> None:
    st.subheader("Tabella per transazione (estrazione → cleaning → giroconti → categorizzazione)")

    table = []
    for tx in result.transactions:
        raw_desc = tx.get("raw_description") or ""
        clean_desc = tx.get("description") or ""
        raw_amount = tx.get("raw_amount")
        amount = tx.get("amount")
        flags = []
        if _sign_flipped(raw_amount, amount):
            flags.append("⚠segno-invertito")
        if (tx.get("category_source") or "") == "fallback":
            flags.append("⚠fallback")
        if tx.get("to_review"):
            flags.append("⚠to-review")
        if _EMOJI_RE.search(clean_desc):
            flags.append("⚠emoji-residua")
        table.append({
            "raw_description": raw_desc,
            "raw_amount": str(raw_amount),
            "→ description": clean_desc,
            "→ amount": str(amount),
            "date": str(tx.get("date")),
            "tx_type": tx.get("tx_type"),
            "giroconto": tx.get("transfer_pair_id") or "",
            "transfer_conf": tx.get("transfer_confidence"),
            "reconciled": tx.get("reconciled"),
            "keyword": tx.get("keyword_matched") or "",
            "category": tx.get("category"),
            "subcategory": tx.get("subcategory"),
            "conf": tx.get("category_confidence"),
            "source": tx.get("category_source"),
            "model": tx.get("category_model"),
            "anomalie": " ".join(flags),
        })

    st.dataframe(table, width="stretch", height=min(560, 80 + 36 * len(table)))

    anomalies = [r for r in table if r["anomalie"]]
    if anomalies:
        st.warning(f"{len(anomalies)} transazioni con anomalie evidenziate (vedi colonna *anomalie*).")
    else:
        st.success("Nessuna anomalia evidenziata sul campione.")

    with st.expander("Dettaglio per transazione (4 blocchi: input → Phase0 → cleaner → categorizer)"):
        sd = schema.model_dump() if (schema is not None and hasattr(schema, "model_dump")) else {}
        for i, tx in enumerate(result.transactions):
            flipped = _sign_flipped(tx.get("raw_amount"), tx.get("amount"))
            head = f"#{i+1} · {(tx.get('description') or tx.get('raw_description') or '')[:60]} · {tx.get('amount')}"
            if flipped:
                head = "⚠ " + head
            with st.expander(head):
                st.markdown("**Input raw**")
                st.json({"raw_description": tx.get("raw_description"),
                         "raw_amount": str(tx.get("raw_amount")),
                         "date": str(tx.get("date")),
                         "currency": tx.get("currency")})
                st.markdown("**Phase 0 (schema applicato)**")
                st.json({"sign_convention": str(sd.get("sign_convention")),
                         "invert_sign": sd.get("invert_sign"),
                         "doc_type": str(sd.get("doc_type"))})
                st.markdown("**Cleaner**")
                st.json({"description": tx.get("description"),
                         "amount_con_segno": str(tx.get("amount")),
                         "segno_invertito_vs_raw": flipped,
                         "tx_type": tx.get("tx_type")})
                st.markdown("**Giroconti / riconciliazione**")
                st.json({"transfer_pair_id": tx.get("transfer_pair_id"),
                         "transfer_confidence": tx.get("transfer_confidence"),
                         "keyword_matched": tx.get("keyword_matched"),
                         "reconciled": tx.get("reconciled")})
                st.markdown("**Categorizer**")
                st.json({"category": tx.get("category"),
                         "subcategory": tx.get("subcategory"),
                         "category_confidence": tx.get("category_confidence"),
                         "category_source": tx.get("category_source"),
                         "category_model": tx.get("category_model"),
                         "to_review": tx.get("to_review")})


# ── Raw LLM I/O per phase ───────────────────────────────────────────────────────

def _render_llm_io(trace) -> None:
    st.subheader("I/O LLM grezzo (prompt + risposta), per fase")
    if not trace:
        st.caption("Nessuna chiamata LLM registrata (schema in cache, regole deterministiche, "
                   "o backend non LLM per le fasi).")
        return
    # Group by phase, preserving pipeline order.
    order = ["classify", "footer", "cleaner", "categorizer"]
    by_phase: dict[str, list] = {}
    for call in trace:
        by_phase.setdefault(call.get("phase", "?"), []).append(call)
    phases = [p for p in order if p in by_phase] + [p for p in by_phase if p not in order]

    for phase in phases:
        calls = by_phase[phase]
        st.markdown(f"**Fase `{phase}` — {len(calls)} chiamata/e LLM**")
        for j, call in enumerate(calls):
            label = f"#{j+1} · {call.get('backend')} ({call.get('model')}) · {call.get('duration_ms')}ms"
            if call.get("error"):
                label = "⚠ " + label
            with st.expander(label):
                if call.get("error"):
                    st.error(f"Errore: {call['error']}")
                st.markdown("**▸ System prompt**")
                st.code(call.get("system_prompt") or "", language="text")
                st.markdown("**▸ User prompt (inviato al modello)**")
                st.code(call.get("user_prompt") or "", language="text")
                st.markdown("**▸ JSON schema richiesto**")
                st.code(json.dumps(call.get("json_schema"), ensure_ascii=False, indent=2, default=str),
                        language="json")
                st.markdown("**▸ Raw response (parsed dict)**")
                st.code(json.dumps(call.get("response"), ensure_ascii=False, indent=2, default=str),
                        language="json")


# ── Export ──────────────────────────────────────────────────────────────────────

def _render_export(result, schema, trace, meta) -> None:
    st.subheader("Export")
    payload = {
        "meta": meta,
        "flow_used": result.flow_used,
        "batch_sha256": result.batch_sha256,
        "needs_schema_review": result.needs_schema_review,
        "schema": schema.model_dump() if (schema is not None and hasattr(schema, "model_dump")) else None,
        "phase_durations_ms": result.phase_durations_ms,
        "totals": {
            "transactions": len(result.transactions),
            "skipped_count": result.skipped_count,
            "merged_count": result.merged_count,
            "internal_transfer_count": result.internal_transfer_count,
            "header_rows_skipped": result.header_rows_skipped,
            "total_file_rows": result.total_file_rows,
        },
        "skipped_rows": [
            {"row_index": sr.row_index, "reason": sr.reason, "raw_values": sr.raw_values}
            for sr in result.skipped_rows
        ],
        "reconciliations": result.reconciliations,
        "transfer_links": result.transfer_links,
        "errors": result.errors,
        "transactions": result.transactions,
        "llm_trace": trace,
    }
    st.download_button(
        "⬇ Scarica trace completo (JSON)",
        data=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        file_name=f"debug_trace_{meta.get('filename', 'file')}.json",
        mime="application/json",
    )
