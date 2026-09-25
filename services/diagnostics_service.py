"""Technical report for support requests: facts about the machine, none about the person.

WHY IT EXISTS
    Someone who reports "the import is slow" or "nothing happens" should not have
    to describe their own computer in prose. Everything that matters here was
    obtainable on 2026-09-23 only by opening a log, querying the database by
    hand and reading a CI workflow, and the whole diagnosis took hours because
    of it.

THE RULE THAT SHAPES EVERYTHING BELOW
    No personal data. Not as a best effort: as a property of how the report is
    built. Every field is named explicitly here, one at a time. Nothing walks an
    object, dumps a table row or copies a path, because those are the ways
    something personal arrives without anyone deciding to include it.

    Specifically excluded, and each for a concrete reason:
      - names of imported files. "revolut_account-statement_2024-02-01_..." names
        the bank and the period.
      - absolute paths. Every one of them contains the account name of whoever
        is running the application. Model files appear by file name only, which
        names the model and nobody else.
      - anything from the transactions themselves: descriptions, amounts,
        counterparties, account names.

    tests/test_diagnostics_report.py asserts this on a generated document. If a
    field is added here without being considered, that test is what should stop
    it.

THE REPORT IS NOT SENT ANYWHERE
    The product promises the data stays on the machine, so this builds a
    document and hands it to the user. Attaching it to a support request is
    their action, not ours.
"""

from __future__ import annotations

import logging
import os
import platform
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger("SPENDIFY")

# Bumped whenever the shape changes. Reports arrive from whatever version the
# sender happens to run: without this, two documents cannot be compared, which
# is precisely when they are needed - a defect that appears on some machines
# only.
SCHEMA_VERSION = "1"

# Settings that describe how inference is configured. Listed rather than
# discovered, because user_settings also holds things that are none of a
# support request's business.
_SETTING_KEYS = (
    "llm_backend",
    "llama_cpp_n_ctx",
    "llama_cpp_n_gpu_layers",
    "llama_cpp_model_path",
    "ollama_model",
    "openai_model",
    "anthropic_model",
)
_PHASES = ("classifier", "cleaner", "categorizer", "footer", "cat")


def _model_name(value: str | None) -> str:
    """A model file by name only: the path around it names the user."""
    if not value:
        return ""
    return os.path.basename(str(value))


def _phase_settings(settings: dict[str, str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for phase in _PHASES:
        entry = {
            "backend": settings.get(f"{phase}_llm_backend", "") or "(inherits)",
            "n_ctx": settings.get(f"{phase}_llama_cpp_n_ctx", "") or "(inherits)",
            "n_gpu_layers": settings.get(f"{phase}_llama_cpp_n_gpu_layers", "") or "(inherits)",
            "model": _model_name(settings.get(f"{phase}_llama_cpp_model_path")),
        }
        out[phase] = entry
    return out


def _import_summary(session: Any) -> dict[str, Any]:
    """Totals and per-row timings over completed jobs.

    The timings are already persisted per phase on import_job; this only reads
    and divides. Seconds per row is the number that says whether a machine
    without acceleration is usable at all, and it is comparable across
    machines in a way that a total duration is not.
    """
    from db.models import ImportJob

    summary: dict[str, Any] = {"jobs": 0, "files": 0, "rows": 0, "per_row_seconds": {}}
    try:
        jobs = (
            session.query(ImportJob)
            .filter(ImportJob.status == "completed")
            .order_by(ImportJob.id.desc())
            .limit(20)
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - a report must not fail on a query
        logger.warning("diagnostics: cannot read import jobs (%s)", exc)
        return summary

    rows = sum(int(j.n_transactions or 0) for j in jobs)
    summary["jobs"] = len(jobs)
    summary["files"] = sum(int(j.n_files or 0) for j in jobs)
    summary["rows"] = rows

    if rows:
        for phase in ("header_detection", "classifying", "footer_detection",
                      "extracting", "cleaning", "categorizing"):
            total_ms = sum(int(getattr(j, f"ms_{phase}", 0) or 0) for j in jobs)
            if total_ms:
                summary["per_row_seconds"][phase] = round(total_ms / 1000.0 / rows, 4)
    return summary


def _counts(session: Any) -> dict[str, int]:
    from db.models import Account, DocumentSchemaModel, ImportJob, Transaction

    out: dict[str, int] = {}
    for label, model in (
        ("transactions", Transaction),
        ("accounts", Account),
        ("saved_formats", DocumentSchemaModel),
        ("import_jobs", ImportJob),
    ):
        try:
            out[label] = int(session.query(model).count())
        except Exception as exc:  # noqa: BLE001
            logger.warning("diagnostics: cannot count %s (%s)", label, exc)
            out[label] = -1
    return out


def _log_summary() -> dict[str, Any]:
    """What the logs would say, without saying it.

    The contents stay out of this document on purpose. A log line carries
    absolute paths, and every absolute path on this machine contains the
    account name of whoever is running the application; it can also carry the
    name of an imported statement, which names a bank and a period. What a
    support request needs first is not the text: it is whether a trace exists
    at all, and in which file, so it can be asked for deliberately.

    Both locations are reported, because they answer different questions. The
    application log is written by the application; the launcher log is the
    redirect of stdout and stderr, and it is where a failure that happens
    before the application is running ends up.
    """
    from support.logging import _resolve_log_dir

    summary: dict[str, Any] = {
        "app_log_files": 0,
        "latest_app_log": "",
        "unhandled_exception_recorded": False,
        "launcher_log_present": False,
        "launcher_log_previous_present": False,
    }

    try:
        log_dir = _resolve_log_dir()
        app_logs = sorted(log_dir.glob("app_*.log"))
        summary["app_log_files"] = len(app_logs)
        if app_logs:
            latest = app_logs[-1]
            # The file name is a timestamp, which is the one part of a path
            # that describes nobody.
            summary["latest_app_log"] = latest.name
            text = latest.read_text(errors="replace")
            summary["unhandled_exception_recorded"] = "Unhandled exception" in text
    except Exception as exc:  # noqa: BLE001 - a report must not fail on a read
        logger.warning("diagnostics: cannot inspect application logs (%s)", exc)

    try:
        import sys as _sys
        from pathlib import Path as _Path

        launcher_dir = (
            _Path.home() / "Library" / "Logs"
            if _sys.platform == "darwin"
            else _Path.home() / ".spendifai"
        )
        launcher_log = launcher_dir / "spendifai-launcher.log"
        summary["launcher_log_present"] = launcher_log.exists()
        summary["launcher_log_previous_present"] = launcher_log.with_suffix(".log.1").exists()
    except Exception as exc:  # noqa: BLE001
        logger.warning("diagnostics: cannot inspect launcher log (%s)", exc)

    return summary


def _percentile(values: list[int], fraction: float) -> int:
    """Order statistic, nearest rank. No interpolation, no library."""
    if not values:
        return 0
    index = min(len(values) - 1, int(round(fraction * (len(values) - 1))))
    return int(values[index])


def _llm_observed(session: Any) -> list[dict[str, Any]]:
    """What the model actually did on this machine, not how it is configured.

    Every other figure about inference in this report is a setting. A setting
    says what was asked for; these rows say what happened, and the two disagree
    exactly when something is wrong. The call log is already written per call
    (db.repository.log_llm_usage), so this reads and groups, it measures
    nothing new.

    CONTEXT PRESSURE IS THE REASON THIS SECTION EXISTS. On 2026-09-22 a saved
    context of 4096 made every import fail with "all backends failed", and the
    diagnosis took hours. prompt_tokens against n_ctx of the same call says it
    in one line, on the machine where it happens.

    Descriptive figures only: counts, median, p95, maximum. The log also
    supports confidence intervals (db.repository.get_token_usage_stats); a
    claim of that kind about a model's behaviour is not what a support document
    is for, and it is not established by reading a machine's own call log.

    source_name is a file name, so it names a bank and a period. It is the one
    column here that must never be read.
    """
    from db.models import LlmUsageLog

    try:
        rows = session.query(LlmUsageLog).all()
    except Exception as exc:  # noqa: BLE001 - a report must not fail on a query
        logger.warning("diagnostics: cannot read the call log (%s)", exc)
        return []

    groups: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        groups.setdefault(
            (row.backend or "", _model_name(row.model_id), row.caller or ""), []
        ).append(row)

    observed: list[dict[str, Any]] = []
    for (backend, model, caller), entries in sorted(groups.items()):
        durations = sorted(int(e.duration_ms or 0) for e in entries)
        prompts = sorted(int(e.prompt_tokens or 0) for e in entries)
        contexts = [int(e.n_ctx) for e in entries if e.n_ctx]
        smallest_context = min(contexts) if contexts else 0
        largest_prompt = prompts[-1] if prompts else 0

        observed.append({
            "backend": backend,
            "model": model,
            "phase": caller,
            "calls": len(entries),
            "median_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
            "median_prompt_tokens": _percentile(prompts, 0.5),
            "max_prompt_tokens": largest_prompt,
            "context": smallest_context,
            # The answer, not the ingredients. A prompt that reaches the
            # context window is the shape of a failure that reports itself as
            # "all backends failed".
            "context_pressure": bool(
                smallest_context and largest_prompt >= 0.9 * smallest_context
            ),
        })

    return observed


def collect(session: Any, settings: dict[str, str]) -> dict[str, Any]:
    """Assemble the report. Never raises: a diagnostic that crashes says nothing."""
    from core import runtime_info
    from services.app_info import get_build_info

    try:
        from core.model_manager import detect_hw

        hw = detect_hw()
    except Exception as exc:  # noqa: BLE001
        logger.warning("diagnostics: hardware detection failed (%s)", exc)
        hw = {}

    version, build_time = get_build_info()
    rt = runtime_info.collect()

    try:
        from services.update_service import _install_method

        install_method = _install_method()
    except Exception:  # noqa: BLE001
        install_method = "unknown"

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "application": {
            "version": version,
            "build_time": build_time,
            "install_method": install_method,
            "packaged_build": rt["packaged_build"],
        },
        "system": {
            "os": hw.get("os", platform.system()),
            "os_version": rt["os_version"],
            "arch": hw.get("arch", platform.machine()),
            "ram_gb": hw.get("ram_gb", 0),
            "emulated_x64_on_arm": rt["emulated_x64_on_arm"],
            "python_version": rt["python_version"],
        },
        "graphics": {
            "gpu": hw.get("gpu", "unknown"),
            "vram_gb": hw.get("vram_gb", 0),
            "gpu_cores": hw.get("gpu_cores", 0),
            # The question a reader actually has. A card being present says
            # nothing: acceleration is a property of the build, not of the
            # machine, and the two disagree more often than one would think.
            "acceleration_active": rt["gpu_acceleration_active"],
            "inference_devices": rt["inference_devices"],
        },
        "inference": {
            "llama_cpp_version": rt["llama_cpp_version"],
            "backend": settings.get("llm_backend", ""),
            "n_ctx": settings.get("llama_cpp_n_ctx", "") or "(auto)",
            "n_gpu_layers": settings.get("llama_cpp_n_gpu_layers", "") or "(auto)",
            "model": _model_name(settings.get("llama_cpp_model_path")),
            "phases": _phase_settings(settings),
        },
        "ledger": _counts(session),
        "imports": _import_summary(session),
        "logs": _log_summary(),
        "llm_observed": _llm_observed(session),
    }


def to_xml(report: dict[str, Any], stars: int | None = None) -> str:
    """Render the report, with the user's rating kept visibly apart.

    The rating is the only value in the document the machine did not measure.
    It lives in its own element so whoever reads the report can tell at a
    glance what was observed from what was said.
    """
    root = ET.Element("spendifai_report", {
        "schema": SCHEMA_VERSION,
        "generated_at": report["generated_at"],
    })

    for section in ("application", "system", "graphics", "inference", "ledger",
                    "imports", "logs"):
        node = ET.SubElement(root, section)
        for key, value in report.get(section, {}).items():
            if isinstance(value, dict):
                sub = ET.SubElement(node, key)
                for k2, v2 in value.items():
                    if isinstance(v2, dict):
                        leaf = ET.SubElement(sub, k2)
                        for k3, v3 in v2.items():
                            ET.SubElement(leaf, k3).text = str(v3)
                    else:
                        ET.SubElement(sub, k2).text = str(v2)
            elif isinstance(value, list):
                sub = ET.SubElement(node, key)
                for v in value:
                    ET.SubElement(sub, "item").text = str(v)
            else:
                ET.SubElement(node, key).text = str(value)

    # A list of groups, not a mapping like every other section, so it is
    # rendered here rather than bent into the loop above.
    observed = ET.SubElement(root, "llm_observed")
    for group in report.get("llm_observed", []):
        node = ET.SubElement(observed, "call_group")
        for key, value in group.items():
            ET.SubElement(node, key).text = str(value)

    if stars is not None:
        ET.SubElement(root, "user_rating", {
            "stars": str(int(stars)),
            "scale": "0-5",
            "declared_by": "user",
        })

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
