"""Import page (RF-08): upload files, run pipeline, show summary."""
from __future__ import annotations

import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st


def _ai_model_still_downloading() -> bool:
    """Read ``~/.spendifai/model_download.status`` and return True if the
    desktop launcher is mid-download.

    Returns False (don't block) when the file is absent — that's the
    normal case for installs without the desktop bundle (Docker, source).
    """
    status_file = Path.home() / ".spendifai" / "model_download.status"
    if not status_file.exists():
        return False
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return False
    if data.get("done"):
        return False
    if data.get("error"):
        return False  # error → not "still downloading", surface elsewhere
    pct = float(data.get("pct", 0.0))
    return pct < 1.0

from services.import_service import (
    SEAL_STAT_KEYS,
    Confidence,
    DocumentSchema,
    DocumentType,
    FileAnalysis,
    ImportService,
    ProcessingConfig,
    SignConvention,
    role_conflicts,
)
from services.rule_service import RuleService
from services.settings_service import SettingsService
from services.transaction_service import TransactionService
from services.update_service import UpdateService
from support.logging import setup_logging
from ui.i18n import t as t_fn
from ui.widgets.immersive import GUIDED_PAGE, go_to
from ui.widgets.staged_import import files_and_rows, staged_files, take_staged_files
from ui.widgets.written_rules import rewrite_pending

logger = setup_logging()

# Minimum seconds between DB progress writes (throttle for high-frequency callbacks)
_DB_WRITE_INTERVAL = 1.5


def _elapsed_text(start, end=None) -> str:
    """How long an import has been running, or took.

    A percentage alone does not tell somebody whether to wait or to go away:
    12% after ten seconds and 12% after four minutes are different situations,
    and only one of them is worth watching. The job row already carries the
    start, so this is a subtraction.

    Stored timestamps come back from SQLite without a timezone even though they
    were written in UTC; comparing one of those against an aware "now" raises.
    Naive values are therefore read as UTC, which is what they are.
    """
    if start is None:
        return ""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end is None:
        end = datetime.now(timezone.utc)
    elif end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    total = int(max(0, (end - start).total_seconds()))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _as_utc(ts):
    """Stored timestamps come back from SQLite naive, though written in UTC."""
    return ts.replace(tzinfo=timezone.utc) if ts is not None and ts.tzinfo is None else ts


def _clock(dt: datetime) -> str:
    """Hour and minute of the day on this computer, e.g. 15:36.

    A duration alone makes the user do the sum; the time of day tells
    whether to wait, go for a coffee or come back after lunch.
    """
    return dt.astimezone().strftime("%H:%M")


# Show the fast estimate beside the safe one only when they differ this much.
_RANGE_MIN_GAP_S = 120


def _if_fast(fast: float, safe: float) -> str:
    """The fast estimate after the safe one ("at 15:05 if..."), or nothing."""
    if safe - fast < _RANGE_MIN_GAP_S:
        return ""
    return t_fn("upload.eta.if_fast", end=_clock_in(fast))


def _clock_in(seconds: float) -> str:
    """The time of day *seconds* from now."""
    from datetime import timedelta
    return _clock(datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds)))


@st.fragment(run_every="2s")
def _render_job_status_poll(import_svc: ImportService) -> None:
    """Auto-refreshing job-status fragment (polls DB every 2 s).

    Renders progress/result widgets without blocking the main script thread,
    so no ghost content from the previous page ever appears.

    Uses session_state to detect state transitions:
    - not running → running : full app rerun to lock the file-uploader form.
    - running → completed/error : full app rerun to unlock the form.
    """
    job = import_svc.get_latest_job()
    was_running: bool = st.session_state.get("_upload_job_was_running", False)

    if job is None:
        st.session_state["_upload_job_was_running"] = False
        return

    if job.status == "running":
        pct = float(job.progress or 0)
        msg = job.status_message or t_fn("upload.processing")
        st.info(f"⏳ {msg}")
        st.progress(pct)
        elapsed = _elapsed_text(job.started_at)
        if elapsed:
            started = _as_utc(job.started_at)
            prior_total, _ = import_svc.estimate_import_seconds(job.n_rows or 0)
            fast, left = import_svc.remaining_range(
                (datetime.now(timezone.utc) - started).total_seconds(), pct, prior_total)
            _show_prominent(st, t_fn("upload.progress_auto_elapsed",
                                     pct=int(pct * 100), elapsed=elapsed,
                                     what=files_and_rows(job.n_files or 1, job.n_rows or 0),
                                     start=_clock(started), end=_clock_in(left))
                            + _if_fast(fast, left))
        else:
            _show_prominent(st, t_fn("upload.progress_auto", pct=int(pct * 100)))
        _render_stall_warning(import_svc, job)
        st.session_state["_upload_job_was_running"] = True
        if not was_running:
            st.rerun()

    elif job.status == "completed":
        st.success(job.status_message or t_fn("upload.completed"))
        st.progress(1.0)
        # The total is worth more than the running count was: it is what the
        # next import on this machine will roughly cost.
        total = _elapsed_text(job.started_at, job.completed_at)
        if total:
            st.caption(t_fn("upload.completed_in", elapsed=total,
                            start=_clock(_as_utc(job.started_at)),
                            end=_clock(_as_utc(job.completed_at or datetime.now(timezone.utc)))))
        if job.detail_message:
            st.caption(job.detail_message)
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()

    elif job.status == "error":
        st.error(job.status_message or t_fn("upload.error_import"))
        if was_running:
            st.session_state["_upload_job_was_running"] = False
            st.rerun()


def _render_stall_warning(import_svc: ImportService, job) -> None:
    """Say when a running import has stopped moving, and let the user end it.

    Without this a hung model left the bar still for hours, and the only way
    to tell slow from dead was a query on the database.
    """
    still = import_svc.seconds_without_progress(job)
    if still < import_svc.STALL_SECONDS:
        return
    st.warning(t_fn("upload.stalled", minutes=int(still // 60)))
    if st.button(t_fn("upload.stalled_stop"), key=f"_stop_stalled_{job.id}"):
        import_svc.stop_job(job.id, t_fn("upload.stalled_stopped"))
        st.rerun()


def _render_guided_continue(import_svc: ImportService) -> None:
    """Inside the workflow, once the import is done: one button to the next step.

    Shown only when nothing waits for the user here (no import running, no
    direction of the amounts to confirm), so the button never skips a
    question the import still has.
    """
    if st.session_state.get("page") != GUIDED_PAGE:
        return
    if st.session_state.get("_pending_sign_confirmations"):
        return
    job = import_svc.get_latest_job()
    if job is None or job.status != "completed":
        return
    st.divider()
    st.markdown(t_fn("upload.guided.next_hint"))
    if st.button(t_fn("upload.guided.next_btn"), type="primary", key="guided_import_continue"):
        go_to("counterparts")


def _skip_reason_labels() -> dict[str, str]:
    return {
        "date_nan": t_fn("upload.skip.date_nan"),
        "date_parse": t_fn("upload.skip.date_parse"),
        "amount_none": t_fn("upload.skip.amount_none"),
        "amount_none_dc": t_fn("upload.skip.amount_none_dc"),
        "balance_row": t_fn("upload.skip.balance_row"),
        "merged": t_fn("upload.skip.merged"),
    }


def _render_last_import_summary():
    results = st.session_state.get("last_import_results")
    if not results:
        return
    st.divider()
    st.subheader(t_fn("upload.summary_title"))
    matched = st.session_state.get("_receipts_matched") or 0
    if matched:
        st.success(t_fn("upload.receipts_matched_one" if matched == 1 else "upload.receipts_matched", n=matched))
    for result in results:
        with st.expander(f"📄 {result.source_name}", expanded=True):
            if result.errors:
                st.error(t_fn("upload.errors") + ": " + "; ".join(result.errors))
            else:
                # ── Row-level breakdown ──
                n_new = len(result.transactions)
                n_dedup = result.skipped_count
                n_skipped = len(result.skipped_rows) if result.skipped_rows else 0
                n_merged = result.merged_count
                n_header = result.header_rows_skipped
                n_total = result.total_file_rows

                n_giro = getattr(result, "internal_transfer_count", 0)

                cols = st.columns(7)
                cols[0].metric(t_fn("upload.metric.file_rows"), n_total + n_header,
                               help=t_fn("upload.metric.file_rows_help"))
                cols[1].metric(t_fn("upload.metric.header"), n_header)
                cols[2].metric(t_fn("upload.metric.imported"), n_new)
                cols[3].metric(t_fn("upload.metric.already_present"), n_dedup)
                cols[4].metric(t_fn("upload.metric.skipped"), n_skipped)
                cols[5].metric(t_fn("upload.metric.aggregated"), n_merged,
                               help=t_fn("upload.metric.aggregated_help"))
                cols[6].metric(t_fn("upload.metric.transfers"), n_giro,
                               help=t_fn("upload.metric.transfers_help"))

                # Sanity check: if numbers don't add up, show a warning
                accounted = n_new + n_dedup + n_skipped + n_merged
                if n_total and accounted < n_total:
                    st.caption(
                        t_fn("upload.unaccounted_rows",
                          unaccounted=n_total - accounted,
                          total=n_total, imported=n_new,
                          dedup=n_dedup, skipped=n_skipped,
                          merged=n_merged)
                    )

                _render_twins(getattr(result, "twins_skipped", None) or [])

                if result.skipped_count and not result.transactions:
                    st.info(t_fn("upload.all_already_present"))
                    continue

                to_review = sum(1 for tx in result.transactions if tx.get("to_review"))
                if to_review:
                    st.warning(t_fn("upload.needs_review", n=to_review))

                if getattr(result, "held_count", 0):
                    st.warning(t_fn("upload.held_future", n=result.held_count))

                # ── Skipped rows detail ──
                if result.skipped_rows:
                    # Group by reason
                    from collections import Counter
                    reason_counts = Counter(s.reason for s in result.skipped_rows)
                    reason_summary = ", ".join(
                        f"{_skip_reason_labels().get(r, r)}: {c}"
                        for r, c in reason_counts.most_common()
                    )
                    st.warning(t_fn("upload.rows_skipped", n=n_skipped, reasons=reason_summary))

                    with st.expander(t_fn("upload.skipped_detail", n=n_skipped), expanded=False):
                        skip_rows_data = [
                            {
                                t_fn("upload.col.row"): s.row_index + 1,
                                t_fn("upload.col.reason"): _skip_reason_labels().get(s.reason, s.reason),
                                **{k: v for k, v in s.raw_values.items()},
                            }
                            for s in result.skipped_rows
                        ]
                        st.dataframe(
                            pd.DataFrame(skip_rows_data),
                            use_container_width=True,
                            hide_index=True,
                        )

                if result.doc_schema:
                    _conf_score = getattr(result.doc_schema, 'confidence_score', 0.0)
                    st.caption(
                        t_fn("upload.schema_summary",
                             doc_type=result.doc_schema.doc_type,
                             account=result.doc_schema.account_label,
                             conf=f"{_conf_score:.2f} ({result.doc_schema.confidence})")
                    )



def _render_twins(twins: list[dict]) -> None:
    """Movements skipped because already saved from another file, one by one.

    The rule (account, date, amount, direction, a word in common) can be
    wrong on two real identical purchases: the user sees every pair it made.
    """
    if not twins:
        return
    with st.expander(t_fn("upload.twins.title", n=len(twins))):
        st.caption(t_fn("upload.twins.caption"))
        st.dataframe(
            pd.DataFrame([{
                t_fn("upload.twins.col_date"): tw["date"],
                t_fn("upload.twins.col_amount"): float(tw["amount"]),
                t_fn("upload.twins.col_this_file"): (tw["description"] or "").replace("\n", " "),
                t_fn("upload.twins.col_saved"): (tw["twin_description"] or "").replace("\n", " "),
                t_fn("upload.twins.col_saved_file"): tw["twin_file"] or "",
            } for tw in twins]),
            hide_index=True, width="stretch",
        )


def _render_sign_confirmation(import_svc: ImportService) -> None:
    """One question, asked once per format, only when the measure is not safe.

    The direction of the amounts is measured on the file. It is applied in
    silence when the file looks like the account the person described; when
    it does not, or the account was never described, somebody has to settle
    it, and the only person who knows is the one whose money it is.

    The answer is remembered against the format, so the same bank never asks
    twice - including the answer "it is already right", which is an answer.
    """
    pending = st.session_state.get("_pending_sign_confirmations") or []
    if not pending:
        return

    for entry in pending:
        key = entry["batch_sha256"]
        with st.container(border=True):
            st.warning(t_fn("sign.title"))
            st.markdown(t_fn("sign.explain").format(
                filename=entry["filename"], n=entry["n_transactions"],
            ))
            c1, c2 = st.columns(2)
            if c1.button(t_fn("sign.flip"), key=f"sign_flip_{key}", type="primary",
                         use_container_width=True):
                with st.spinner(t_fn("sign.recategorizing")):
                    moved = import_svc.confirm_sign(
                        entry["batch_sha256"], entry["source_identifier"], flip=True,
                    )
                st.session_state["_pending_sign_confirmations"] = [
                    e for e in pending if e["batch_sha256"] != key
                ]
                st.success(t_fn("sign.flipped").format(n=moved))
                st.rerun()
            if c2.button(t_fn("sign.keep"), key=f"sign_keep_{key}",
                         use_container_width=True):
                import_svc.confirm_sign(
                    entry["batch_sha256"], entry["source_identifier"], flip=False,
                )
                st.session_state["_pending_sign_confirmations"] = [
                    e for e in pending if e["batch_sha256"] != key
                ]
                st.success(t_fn("sign.kept"))
                st.rerun()


def _render_schema_review(import_svc: ImportService, config: ProcessingConfig) -> bool:
    """Show editable schema form for files with medium/low confidence.
    Returns True if the user confirmed and re-import was triggered."""
    pending = st.session_state.get("_pending_schema_reviews", [])
    if not pending:
        return False

    # Deduplica per filename
    seen: set[str] = set()
    pending = [e for e in pending if not (e["filename"] in seen or seen.add(e["filename"]))]  # type: ignore[func-returns-value]

    st.warning(t_fn("upload.schema_review_warning", n=len(pending)))

    doc_type_options = [d.value for d in DocumentType]
    sign_options = [s.value for s in SignConvention]
    none_option = t_fn("upload.schema.none_option")

    confirmed_schemas: dict[str, DocumentSchema] = {}
    has_role_conflict = False

    for _idx, entry in enumerate(pending):
        filename = entry["filename"]
        _key = f"{_idx}_{filename}"
        result = entry["result"]
        schema = result.doc_schema
        cols = result.available_columns
        col_options = [none_option] + cols

        evidence = schema.semantic_evidence if schema else []
        _conf_score = getattr(schema, 'confidence_score', 0.0) if schema else 0.0
        confidence_label = schema.confidence.value if schema else "unknown"

        with st.expander(t_fn("upload.file_with_confidence", filename=filename,
                      conf=_conf_score, label=confidence_label), expanded=True):
            if evidence:
                st.caption(t_fn("upload.schema.llm_reasoning") + " " + " · ".join(evidence))

            # Raw file preview — preheader (top) and footer (bottom) in collapsible sections
            try:
                _skip = schema.skip_rows if schema else 0
                _n_preview = max(30, _skip + 15)
                df_raw_preview = import_svc.get_raw_head(entry["raw_bytes"], filename, n=_n_preview)
                if _skip > 0 and len(df_raw_preview) > 0:
                    _preheader_df = df_raw_preview.iloc[:_skip]
                    if not _preheader_df.empty:
                        with st.expander(t_fn("upload.schema.preheader", n=_skip), expanded=False):
                            st.dataframe(_preheader_df, use_container_width=True, hide_index=False)
                # Show data portion (first 10 rows after header)
                st.markdown(t_fn("upload.schema.data_preview"))
                _data_start = _skip
                _data_end = min(_data_start + 10, len(df_raw_preview))
                st.dataframe(
                    df_raw_preview.iloc[_data_start:_data_end],
                    use_container_width=True, hide_index=False,
                )
                # Footer preview: show last 5 rows of the raw file
                _total_raw = import_svc.get_raw_head(entry["raw_bytes"], filename, n=500)
                if len(_total_raw) > 10:
                    _footer_df = _total_raw.tail(5)
                    with st.expander(t_fn("upload.schema.footer_preview"), expanded=False):
                        st.dataframe(_footer_df, use_container_width=True, hide_index=False)
            except Exception as e:
                st.caption(t_fn("upload.schema.preview_unavailable", error=e))

            c1, c2 = st.columns(2)
            doc_type_val = schema.doc_type.value if schema else doc_type_options[0]
            doc_type_sel = c1.selectbox(
                t_fn("upload.schema.doc_type"), doc_type_options,
                index=doc_type_options.index(doc_type_val) if doc_type_val in doc_type_options else 0,
                key=f"rev_doc_type_{_key}",
            )
            account_sel = c2.text_input(
                t_fn("upload.schema.account_label"), value=schema.account_label if schema else "",
                key=f"rev_account_{_key}",
            )

            c3, c4, c5 = st.columns(3)

            def _col_idx(val):
                return col_options.index(val) if val in col_options else 0

            amount_sel = c3.selectbox(
                t_fn("upload.schema.amount_col"), col_options,
                index=_col_idx(schema.amount_col if schema else none_option),
                key=f"rev_amount_{_key}",
            )
            date_sel = c4.selectbox(
                t_fn("upload.schema.date_col"), col_options,
                index=_col_idx(schema.date_col if schema else none_option),
                key=f"rev_date_{_key}",
            )
            sign_val = schema.sign_convention.value if schema else sign_options[0]
            sign_sel = c5.selectbox(
                t_fn("upload.schema.sign_convention"), sign_options,
                index=sign_options.index(sign_val) if sign_val in sign_options else 0,
                key=f"rev_sign_{_key}",
            )

            c6, c7, c8, c9 = st.columns(4)
            description_sel = c6.selectbox(
                t_fn("upload.schema.description_col"), col_options,
                index=_col_idx(schema.description_col if schema and schema.description_col else none_option),
                key=f"rev_description_{_key}",
            )
            debit_sel = c7.selectbox(
                t_fn("upload.schema.debit_col"), col_options,
                index=_col_idx(schema.debit_col if schema and schema.debit_col else none_option),
                key=f"rev_debit_{_key}",
            )
            credit_sel = c8.selectbox(
                t_fn("upload.schema.credit_col"), col_options,
                index=_col_idx(schema.credit_col if schema and schema.credit_col else none_option),
                key=f"rev_credit_{_key}",
            )
            invert = c9.checkbox(
                t_fn("upload.schema.invert_sign"), value=schema.invert_sign if schema else False,
                key=f"rev_invert_{_key}",
            )

            # Build confirmed schema from user selections
            base = schema.model_copy() if schema else DocumentSchema(
                doc_type=DocumentType(doc_type_options[0]),
                date_col="", amount_col="", sign_convention=SignConvention(sign_options[0]),
                date_format="%d/%m/%Y", account_label="", confidence=Confidence.high,
            )
            confirmed_schemas[filename] = base.model_copy(update={
                "doc_type":         DocumentType(doc_type_sel),
                "account_label":    account_sel,
                "amount_col":       "" if amount_sel == none_option else amount_sel,
                "date_col":         "" if date_sel == none_option else date_sel,
                "description_col":  None if description_sel == none_option else description_sel,
                "sign_convention":  SignConvention(sign_sel),
                "debit_col":        None if debit_sel == none_option else debit_sel,
                "credit_col":       None if credit_sel == none_option else credit_sel,
                "invert_sign":      invert,
                "confidence":       Confidence.high,
                "confidence_score": 1.0,  # user-confirmed schema is fully trusted
                "skip_rows":        schema.skip_rows if schema else 0,
                "header_sha256":    entry.get("header_sha256", ""),
            })

            # One column, one role. A field the form does not show yields to
            # the person's visible choice; a clash between visible fields is
            # theirs to resolve, so the import waits until they do.
            _chosen = confirmed_schemas[filename]
            _visible = {
                _chosen.amount_col, _chosen.date_col, _chosen.description_col,
                _chosen.debit_col, _chosen.credit_col,
            } - {None, ""}
            if _chosen.date_accounting_col in _visible:
                confirmed_schemas[filename] = _chosen = _chosen.model_copy(
                    update={"date_accounting_col": None}
                )
            _role_label = {
                "date_col":        t_fn("upload.schema.date_col"),
                "amount_col":      t_fn("upload.schema.amount_col"),
                "debit_col":       t_fn("upload.schema.debit_col"),
                "credit_col":      t_fn("upload.schema.credit_col"),
                "description_col": t_fn("upload.schema.description_col"),
            }
            for _col, _roles in role_conflicts(_chosen.model_dump()).items():
                has_role_conflict = True
                st.error(t_fn(
                    "upload.schema.role_conflict",
                    column=_col,
                    roles=", ".join(_role_label.get(r, r) for r in _roles),
                ))

            # Normalised preview
            st.markdown(t_fn("upload.schema.normalized_preview"))
            try:
                _skip_override = (schema.skip_rows or None) if schema else None
                preview_txs = import_svc.get_normalized_preview(
                    entry["raw_bytes"], filename,
                    confirmed_schemas[filename],
                    n=30,
                    skip_rows_override=_skip_override,
                )
                if preview_txs:
                    _col_date = t_fn("ledger.col.date")
                    _col_desc = t_fn("ledger.col.description")
                    _col_in   = t_fn("ledger.col.income")
                    _col_out  = t_fn("ledger.col.expense")
                    _col_cur  = t_fn("upload.col.currency")
                    _col_raw_desc = t_fn("upload.col.raw_description")
                    _col_raw_amt  = t_fn("upload.col.raw_amount")
                    preview_rows = [
                        {
                            _col_date:     str(tx.get("date", "")),
                            _col_desc:     (tx.get("description") or "")[:80],
                            _col_in:       float(tx["amount"]) if tx.get("amount") is not None and float(tx["amount"]) > 0 else None,
                            _col_out:      abs(float(tx["amount"])) if tx.get("amount") is not None and float(tx["amount"]) < 0 else None,
                            _col_cur:      tx.get("currency", "EUR"),
                            _col_raw_desc: (tx.get("raw_description") or "")[:80],
                            _col_raw_amt:  str(tx.get("raw_amount", "")),
                        }
                        for tx in preview_txs[:8]
                    ]
                    st.dataframe(
                        pd.DataFrame(preview_rows),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            _col_in:  st.column_config.NumberColumn(_col_in,  format="%.2f"),
                            _col_out: st.column_config.NumberColumn(_col_out, format="%.2f"),
                        },
                    )
                else:
                    st.warning(t_fn("upload.schema.no_rows_parsed"))
            except Exception as e:
                st.caption(t_fn("upload.schema.preview_unavailable", error=e))

    if st.button(
        t_fn("upload.schema.confirm_btn"), type="primary", disabled=has_role_conflict,
    ):
        # Every job that paused for this review, also those whose file was a
        # duplicate dropped above: each one must stop saying "waiting".
        all_pending = st.session_state.get("_pending_schema_reviews", [])
        paused_jobs = {e["job_id"] for e in all_pending if e.get("job_id") is not None}

        results = []
        added_per_job: dict[int, int] = {}
        skipped_per_job: dict[int, int] = {}
        prog = st.progress(0.0)
        for i, entry in enumerate(pending):
            raw_bytes = entry["raw_bytes"]
            filename = entry["filename"]
            confirmed = confirmed_schemas[filename]
            prog.progress((i + 1) / len(pending))

            result = import_svc.process_file_single(
                raw_bytes=raw_bytes,
                filename=filename,
                config=config,
                known_schema=confirmed,
                account_label_override=entry.get("account_label_override"),
            )
            import_svc.persist_result(result)
            results.append(result)
            if entry.get("job_id") is not None:
                jid = entry["job_id"]
                added_per_job[jid] = added_per_job.get(jid, 0) + len(result.transactions)
                skipped_per_job[jid] = skipped_per_job.get(jid, 0) + result.skipped_count

        st.session_state["_pending_schema_reviews"] = []
        existing = st.session_state.get("last_import_results", [])
        st.session_state["last_import_results"] = existing + results

        # Update job status so the banner shows ✅ instead of ⏸️. The total
        # includes the movements the job had already imported on its own.
        for jid in paused_jobs:
            n_total = import_svc.add_job_transactions(jid, added_per_job.get(jid, 0))
            if n_total is None:
                continue
            msg = t_fn("upload.completed_tx", n=f"{n_total:,}")
            if skipped_per_job.get(jid):
                msg += t_fn("upload.already_present_skipped", n=skipped_per_job[jid])
            import_svc.update_job(jid, status_message=msg)

        st.rerun()

    return True


def _fmt_duration(seconds: float) -> str:
    h, m = ImportService.duration_parts(seconds)
    if not h and not m:
        return t_fn("common.duration.under_minute")
    if not h:
        return t_fn("common.duration.minutes", m=m)
    return t_fn("common.duration.hours", h=h, m=m)


def _render_estimate(import_svc: ImportService, files: list[tuple[str, bytes]]) -> None:
    """The expected duration, before the import starts."""
    try:
        n_rows = import_svc.count_rows(files)
        secs, from_history = import_svc.estimate_import_seconds(n_rows)
    except Exception:  # an estimate must never stop an import
        return
    key = "upload.eta.history" if from_history else "upload.eta.first"
    _show_prominent(st, t_fn(key, what=files_and_rows(len(files), n_rows),
                             time=_fmt_duration(secs), end=_clock_in(secs)))


def _show_prominent(target, text: str, strong: str | None = None) -> None:
    """Progress and time left in a larger font: it is what the user waits on.

    ``target`` is ``st`` or an ``st.empty()`` slot; ``strong`` is shown in bold
    after ``text``.
    """
    body = html.escape(text)
    if strong:
        body += " · <b>" + html.escape(strong) + "</b>"
    target.markdown(
        f'<div style="font-size:1.25rem;line-height:1.5">{body}</div>',
        unsafe_allow_html=True,
    )


def _run_import(
    import_svc: ImportService,
    items: list[tuple[str, bytes]],
    _file_account_map: dict[str, str | None],
    _file_skip_map: dict[str, int],
) -> None:
    """Run the import of ``items`` (name, bytes) with progress and job record.

    Shared by the Import page button and by the files prepared account by
    account in the Check List, which arrive with their account already fixed.
    """
    st.session_state["llm_in_progress"] = True
    giroconto_mode = st.session_state.get("giroconto_mode", "neutral")
    config = import_svc.build_config(giroconto_mode=giroconto_mode)

    # Analyse each file (schema cache lookup)
    files: list[tuple[bytes, str, int]] = []
    known_schemas: dict[str, object] = {}
    file_header_sha256: dict[str, str] = {}

    for name, raw_bytes in items:
        analysis: FileAnalysis = import_svc.analyze_file(raw_bytes, name)
        file_header_sha256[name] = analysis.header_sha256
        if analysis.known_schema:
            logger.info(f"upload_page: schema cache hit for {name}")
            known_schemas[name] = analysis.known_schema
        files.append((raw_bytes, name, analysis.n_rows))

    total_files = len(files)
    total_rows = import_svc.count_rows(items)
    # Payment intermediaries learned on all the files together, before any
    # description is cleaned: the counterparts step finds the merchants.
    import_svc.learn_intermediaries(items)
    prior_total, _ = import_svc.estimate_import_seconds(total_rows)
    run_started = time.monotonic()
    started_at = _clock(datetime.now(timezone.utc))

    # Create job record
    job = import_svc.create_job(n_files=total_files, n_rows=total_rows or None)
    job_id = job.id

    # Live progress widgets for this session
    _progress_bar = st.progress(0.0)
    _status_text  = st.empty()
    _counter_text = st.empty()

    results = []
    # Each file's share of the run goes by its rows: the time left is for the
    # whole import, and must not follow the size of the file in progress.
    spans = import_svc.file_spans([n for _, _, n in files])
    for i, (raw_bytes, filename, _n_rows) in enumerate(files):
        file_start, file_end = spans[i]

        _status_text.text(f"File {i + 1}/{total_files} - {filename}")
        _counter_text.caption(t_fn("upload.starting"))

        import_svc.update_job(
            job_id,
            progress=file_start,
            status_message=f"File {i + 1}/{total_files} - {filename}",
        )

        _last_db_write = [0.0]

        def _make_cb(start: float, end: float, fname: str,
                     fidx: int, ftot: int, jid: int, _last: list):
            def _cb(p: float, phase: str | None = None):
                pct = start + (end - start) * p
                _progress_bar.progress(min(pct, 1.0))
                _status_text.text(f"File {fidx + 1}/{ftot} - {fname}")
                fast, left = import_svc.remaining_range(
                    time.monotonic() - run_started, pct, prior_total)
                left_text = t_fn("upload.eta.remaining", time=_fmt_duration(left),
                                 what=files_and_rows(ftot, total_rows), end=_clock_in(left),
                                 start=started_at) + _if_fast(fast, left)
                if phase:
                    progress_text = t_fn("upload.file_progress_with_phase",
                                         phase=t_fn(f"upload.phase.{phase}"),
                                         pct=int(p * 100))
                else:
                    progress_text = t_fn("upload.file_progress", pct=int(p * 100))
                _show_prominent(_counter_text, progress_text, left_text)
                now = time.time()
                if now - _last[0] >= _DB_WRITE_INTERVAL:
                    _last[0] = now
                    phase_suffix = (
                        f" - {t_fn(f'upload.phase.{phase}')}" if phase else ""
                    )
                    import_svc.update_job(
                        jid,
                        progress=round(pct, 4),
                        status_message=(
                            f"File {fidx + 1}/{ftot} - {fname}"
                            f"{phase_suffix} ({int(p * 100)}%)"
                        ),
                    )
            return _cb

        try:
            result = import_svc.process_file_single(
                raw_bytes=raw_bytes,
                filename=filename,
                config=config,
                known_schema=known_schemas.get(filename),
                progress_callback=_make_cb(
                    file_start, file_end, filename, i, total_files,
                    job_id, _last_db_write,
                ),
                account_label_override=_file_account_map.get(filename),
                skip_rows_override=_file_skip_map.get(filename),
            )
        except Exception as exc:
            # Most common cause here: llama-cpp-python raises ValueError
            # ("Failed to load model from file: ...") when a GGUF is
            # truncated / corrupted / missing. Surface a friendly error
            # instead of a Streamlit splash + traceback, and mark the
            # job as errored so the next session doesn't think it's
            # still running.
            msg = t_fn("upload.error_backend_load",
                       filename=filename, error=str(exc)[:300])
            logger.error(
                f"upload_page: process_file_single failed for {filename}: "
                f"{type(exc).__name__}: {exc}"
            )
            st.error(msg)
            import_svc.update_job(
                job_id,
                status="error",
                status_message=str(exc)[:200],
                completed_at=datetime.now(timezone.utc),
            )
            st.session_state["llm_in_progress"] = False
            return

        if result.needs_schema_review:
            pending = st.session_state.get("_pending_schema_reviews", [])
            pending.append({
                "raw_bytes":              raw_bytes,
                "filename":               filename,
                "result":                 result,
                "account_label_override": _file_account_map.get(filename),
                "header_sha256":          file_header_sha256.get(filename, ""),
                "job_id":                 job_id,
            })
            st.session_state["_pending_schema_reviews"] = pending
        else:
            import_svc.persist_result(result)
            # Show auto-import success for high-confidence Flow 2 files
            if result.flow_used == "flow2" and result.doc_schema:
                _auto_score = getattr(result.doc_schema, 'confidence_score', 0.0)
                if _auto_score >= 0.80:
                    st.success(
                        t_fn("upload.auto_imported", filename=filename,
                             score=f"{_auto_score:.2f}",
                             confidence=result.doc_schema.confidence)
                    )

        results.append(result)
        _progress_bar.progress(file_end)
        import_svc.update_job(job_id, progress=file_end)

    # Only this job's files: reviews left over from earlier jobs are theirs.
    n_pending = sum(1 for r in results if r.needs_schema_review)
    n_tx      = sum(len(r.transactions) for r in results if not r.needs_schema_review)
    n_skipped = sum(r.skipped_count for r in results)

    if n_pending:
        final_msg = t_fn("upload.pending_review", n=n_pending)
    else:
        final_msg = t_fn("upload.completed_tx", n=n_tx)
    if n_skipped:
        final_msg += t_fn("upload.already_present_skipped", n=n_skipped)

    final_detail = t_fn("upload.final_detail",
                        n_files=total_files,
                        time=datetime.now(timezone.utc).strftime('%H:%M:%S'))

    # AI-88: accumulate per-phase wall-clock across all files of this
    # job. Each ImportResult carries `phase_durations_ms` populated by
    # orchestrator.process_file. Sum them so the job row reports the
    # total time spent in each pipeline phase across the whole batch.
    _phase_totals: dict[str, int] = {}
    for r in results:
        for phase, ms in (getattr(r, "phase_durations_ms", None) or {}).items():
            _phase_totals[phase] = _phase_totals.get(phase, 0) + int(ms)
    _seal_totals: dict[str, int] = {}
    for r in results:
        for key, count in (getattr(r, "cleaner_seal", None) or {}).items():
            _seal_totals[key] = _seal_totals.get(key, 0) + int(count)

    import_svc.update_job(
        job_id,
        status="completed",
        progress=1.0,
        status_message=final_msg,
        detail_message=final_detail,
        n_transactions=n_tx,
        completed_at=datetime.now(timezone.utc),
        ms_header_detection=_phase_totals.get("header_detection"),
        ms_classifying     =_phase_totals.get("classifying"),
        ms_footer_detection=_phase_totals.get("footer_detection"),
        ms_extracting      =_phase_totals.get("extracting"),
        ms_cleaning        =_phase_totals.get("cleaning"),
        ms_categorizing    =_phase_totals.get("categorizing"),
        **{f"cleaner_{key}": _seal_totals.get(key) for key in SEAL_STAT_KEYS},
    )

    st.session_state["llm_in_progress"] = False
    st.session_state["last_import_results"] = [r for r in results if not r.needs_schema_review]
    # The model is free again: rewrite the rules it could not write at a tick
    # (spec 18). Never let this stop the end of an import.
    try:
        rewrite_pending(RuleService(import_svc.engine), TransactionService(import_svc.engine))
    except Exception as exc:
        logger.warning(f"upload_page: rewriting pending rules failed: {exc}")
    # Receipts saved in the expense confirmation may have their movement now
    # (spec 32, 4.4bis): said here, checked by the user in that page. Never
    # let this stop the end of an import either.
    try:
        from services.expense_confirm_service import ExpenseConfirmService
        st.session_state["_receipts_matched"] = len(ExpenseConfirmService(import_svc.engine).receipt_matches())
    except Exception as exc:
        logger.warning(f"upload_page: matching the saved receipts failed: {exc}")
    # The direction of the amounts, asked about only where the measure could
    # not be applied in silence. One entry per format, not per file.
    st.session_state["_pending_sign_confirmations"] = [
        {
            "filename": r.source_name,
            "batch_sha256": r.batch_sha256,
            "source_identifier": getattr(r.doc_schema, "source_identifier", ""),
            "n_transactions": len(r.transactions),
        }
        for r in results
        if r.sign_needs_confirmation and r.doc_schema is not None
    ]


# The files prepared in the Check List start in a run of their own. Streamlit
# drops the elements of the page the user came from only when a run ends
# normally: an import started in the run that arrives here would keep the
# Check List grid on screen, faded, under the import for its whole length.
# This run shows the files and ends; a fragment ticking a second later runs
# them. The maps travel in the armed state, the outcome flag tells the next
# full run to show what follows an import.
_STAGED_ARMED = "_staged_import_armed"
_STAGED_DONE = "_staged_import_done"
_STAGED_GO = "_staged_import_go"


def _run_if_armed(import_svc: ImportService) -> bool:
    """Run the held files if a finished run armed them. True once run."""
    armed = st.session_state.pop(_STAGED_ARMED, None)
    if armed is None:
        return False
    files = take_staged_files()
    _run_import(import_svc, [(f.name, f.data) for f in files], *armed)
    st.session_state[_STAGED_DONE] = True
    return True


@st.fragment(run_every=1)
def _staged_starter(import_svc: ImportService) -> None:
    if _run_if_armed(import_svc):
        st.rerun()


def _start_after_this_run(import_svc: ImportService, account_map: dict, skip_map: dict) -> None:
    """Arm the held files for the fragment's next tick, never this run.

    The fragment also runs inline here: it is called before arming, so it
    finds nothing to do until this run has ended.
    """
    st.session_state.pop(_STAGED_ARMED, None)
    _staged_starter(import_svc)
    st.session_state[_STAGED_ARMED] = (account_map, skip_map)


def _render_staged(import_svc: ImportService, staged: list) -> None:
    """Files on hold with their account. Starts them, asking first only where
    the header of a new format could not be found on its own."""
    st.info(t_fn("upload.staged.title"))
    account_map: dict[str, str | None] = {}
    skip_map: dict[str, int] = {}
    asks = False
    for f in staged:
        account_map[f.name] = f.account
        hit = import_svc.find_schema_by_header(f.data, f.name)
        detected, certain = import_svc.detect_skip_rows(f.data, f.name)
        c1, c2, c3 = st.columns([3, 2, 2], vertical_alignment="center")
        c1.caption(f"📄 {f.name}")
        c2.markdown(f"**{f.account}**")
        if hit or certain:
            skip_map[f.name] = hit.skip_rows if hit else detected
            continue
        asks = True
        if f.data.count(b"\n") < 50:
            st.warning(t_fn("upload.first_upload_warning", filename=f.name,
                            rows=f.data.count(b"\n")))
        skip_map[f.name] = c3.number_input(
            t_fn("upload.skip_rows_label"), min_value=0, max_value=20, value=0,
            key=f"file_skip_{f.name}", help=t_fn("upload.skip_rows_help"),
        )
    _render_estimate(import_svc, [(f.name, f.data) for f in staged])
    # Once the import is about to start the buttons go: "Clear and pick other
    # files" pressed during the import would leave it half done. A press on
    # "Start" reruns first, so the run that arms the import draws no buttons.
    if not asks or st.session_state.pop(_STAGED_GO, False):
        _start_after_this_run(import_svc, account_map, skip_map)
        return
    col_start, col_clear = st.columns([1, 3])
    if col_clear.button(t_fn("upload.staged.clear"), key="staged_clear"):
        take_staged_files()
        st.rerun()
    if col_start.button(t_fn("upload.staged.start"), type="primary", key="staged_start"):
        st.session_state[_STAGED_GO] = True
        st.rerun()


def render_upload_page(engine):
    st.header(t_fn("upload.title"))

    import_svc = ImportService(engine)
    cfg_svc    = SettingsService(engine)

    if cfg_svc.get("import_test_mode", "false").lower() == "true":
        st.error(t_fn("upload.test_mode_banner"))

    # Guard: owner names must be configured before any import
    if not import_svc.get_owner_names():
        st.error(t_fn("upload.owner_names_missing"))
        return

    # Guard: the AI model must be present before we can import & categorise.
    # The desktop launcher downloads it in the background on first run; until
    # it's done, surface a clear "please wait" instead of letting the user
    # upload and then watch the LLM call fail.
    # Emulazione x64 su Windows ARM: l'import parte ma l'inferenza locale e'
    # cosi' lenta da sembrare un blocco. Si avvisa e si lascia procedere: e'
    # una macchina lenta, non una configurazione vietata.
    try:
        if UpdateService(engine).runs_emulated():
            st.warning(t_fn("upload.emulated_arm"), icon="🐢")
    except Exception:
        pass

    if _ai_model_still_downloading():
        st.warning(
            t_fn("upload.model_downloading"),
            icon="📚",
        )
        return

    # Check for an active running job before rendering the form.
    active_job = import_svc.get_latest_job()
    if active_job and active_job.status == "running":
        st.info(t_fn("upload.job_running"))
        _render_job_status_poll(import_svc)
        return

    # Files prepared account by account in the Check List: the account is
    # fixed, this page only adjusts header and footer, runs and reports.
    staged = staged_files()
    if staged:
        _render_staged(import_svc, staged)
        return
    if st.session_state.pop(_STAGED_DONE, False):
        _render_after_import(import_svc)
        return

    uploaded_files = st.file_uploader(
        t_fn("upload.file_uploader_label"),
        type=["csv", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        # Empty-state amichevole quando il DB non ha ancora transazioni:
        # è il caso classico del post-onboarding "Lo faccio dopo". Una volta
        # importata anche una sola transazione, mostriamo invece il normale
        # hint del file picker.
        if not TransactionService(engine).has_transactions():
            st.markdown(f"### {t_fn('upload.empty_state.title')}")
            st.caption(t_fn("upload.empty_state.caption"))
        else:
            st.info(t_fn("upload.no_files_hint"))
        _render_job_status_poll(import_svc)
        _render_last_import_summary()
        return

    # Per-file account selector
    _accounts     = cfg_svc.get_accounts()
    _account_names  = [a.name for a in _accounts]
    _account_options = [t_fn("upload.auto_detect")] + _account_names

    if not _account_names:
        st.warning(t_fn("upload.no_accounts_warning"))

    st.markdown(t_fn("upload.associate_files"))
    _file_account_map: dict[str, str | None] = {}
    _file_skip_map: dict[str, int] = {}

    for uf in uploaded_files:
        raw_preview = uf.getvalue()
        _detected_skip, _skip_certain = import_svc.detect_skip_rows(raw_preview, uf.name)
        _sha256_hit = import_svc.find_schema_by_header(raw_preview, uf.name)

        # ── I-02: auto-select account from cached schema ─────────────
        _auto_account_idx = 0  # default: "— rilevamento automatico —"
        if _sha256_hit and getattr(_sha256_hit, "account_label", None):
            _cached_label = _sha256_hit.account_label
            if _cached_label in _account_names:
                _auto_account_idx = _account_names.index(_cached_label) + 1  # +1 for the "auto" option

        if _sha256_hit or _skip_certain:
            c1, c2 = st.columns([3, 2])
            c1.caption(f"📄 {uf.name}")
            sel = c2.selectbox(
                t_fn("ledger.col.account"),
                options=_account_options,
                index=_auto_account_idx,
                key=f"file_account_{uf.name}",
                label_visibility="collapsed",
            )
            _file_skip_map[uf.name] = _sha256_hit.skip_rows if _sha256_hit else _detected_skip
        else:
            # ── I-01: warning primo caricamento ──────────────────────
            _row_count = raw_preview.count(b"\n") if isinstance(raw_preview, bytes) else 50
            if _row_count < 50:
                st.warning(t_fn("upload.first_upload_warning", filename=uf.name, rows=_row_count))
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.caption(f"📄 {uf.name}")
            sel = c2.selectbox(
                t_fn("ledger.col.account"),
                options=_account_options,
                index=_auto_account_idx,
                key=f"file_account_{uf.name}",
                label_visibility="collapsed",
            )
            _file_skip_map[uf.name] = c3.number_input(
                t_fn("upload.skip_rows_label"),
                min_value=0,
                max_value=20,
                value=0,
                key=f"file_skip_{uf.name}",
                help=t_fn("upload.skip_rows_help"),
            )
        _file_account_map[uf.name] = sel if sel != t_fn("upload.auto_detect") else None

    _render_estimate(import_svc, [(uf.name, uf.getvalue()) for uf in uploaded_files])
    if st.button(t_fn("upload.process_btn"), type="primary"):
        _run_import(import_svc, [(uf.name, uf.read()) for uf in uploaded_files],
                    _file_account_map, _file_skip_map)

    _render_after_import(import_svc)


def _render_after_import(import_svc: ImportService) -> None:
    """What follows a run: sign question, format review, progress, report."""
    # The direction of the amounts, when the two readings disagreed.
    _render_sign_confirmation(import_svc)

    # Schema review gate
    if st.session_state.get("_pending_schema_reviews"):
        giroconto_mode = st.session_state.get("giroconto_mode", "neutral")
        config = import_svc.build_config(giroconto_mode=giroconto_mode)
        _render_schema_review(import_svc, config)
        return

    _render_job_status_poll(import_svc)
    _render_last_import_summary()
    _render_guided_continue(import_svc)
