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
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

logger = logging.getLogger("SPENDIFY")

# Bumped whenever the shape changes. Reports arrive from whatever version the
# sender happens to run: without this, two documents cannot be compared, which
# is precisely when they are needed - a defect that appears on some machines
# only.
#
# 2: the operating system by name (os_name, os_id) and the last import on its
#    own, next to the average over the last twenty.
# 3: categorization, whether the import produced categories and how varied.
# 4: description_cleaner, what the seal on the cleaner refused (spec 35); in
#    inference, who chose the local model, whether it is a tested one, the
#    recommended one, and the model each phase really loads (spec 36, 7).
SCHEMA_VERSION = "4"

# Where test reports are filed. The form behind it reads the document and adds
# the machine to the compatibility table, so nobody copies a value by hand.
ISSUE_FORM_URL = "https://github.com/spendifai/spendif-ai/issues/new"
ISSUE_TEMPLATE = "test_report.yml"

# A browser and GitHub both accept a few thousand characters in an address,
# not an unlimited number. Past this the form opens empty and the page says to
# paste the document, rather than producing a link that fails on some systems.
_MAX_ISSUE_URL = 7500

# The same address the page offers for help. A test report sent there is read
# by the same script as one filed on GitHub, from the saved email.
REPORT_MAIL_ADDRESS = "support@spendif.ai"

# The steps a tester ticks, worded exactly as in the issue form: the script
# that reads reports matches these labels, in the form and in an email alike,
# so they stay in English whatever the language of the page.
TEST_OUTCOMES = (
    "Installed",
    "Started from the application menu or icon",
    "Imported a statement",
    "Uninstalled cleanly",
)

_PHASE_COLUMNS = ("header_detection", "classifying", "footer_detection",
                  "extracting", "cleaning", "categorizing")

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


def _phase_settings(settings: dict[str, str], in_use: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-phase backend knobs, with the model each phase really loads.

    Args:
        settings: the user settings.
        in_use: LocalModelInUse by phase ("main" and the phases with their own
            local backend), from llm_service.local_models_in_use().
    """
    out: dict[str, dict[str, Any]] = {}
    for phase in _PHASES:
        # A phase without its own backend runs on the main one; "cat" is the
        # legacy name of the categorizer.
        loaded = in_use.get("categorizer" if phase == "cat" else phase) or in_use.get("main")
        entry = {
            "backend": settings.get(f"{phase}_llm_backend", "") or "(inherits)",
            "n_ctx": settings.get(f"{phase}_llama_cpp_n_ctx", "") or "(inherits)",
            "n_gpu_layers": settings.get(f"{phase}_llama_cpp_n_gpu_layers", "") or "(inherits)",
            "model": _model_name(loaded.path) if loaded else "",
            "in_catalog": loaded.in_catalog if loaded else "",
        }
        out[phase] = entry
    return out


def _model_in_use(settings: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The local models in use by phase, and the main model's report fields (spec 36, 7).

    Args:
        settings: the user settings.

    Returns:
        (LocalModelInUse by phase, fields for the inference section). A report
        must not fail: any error leaves both empty.
    """
    try:
        from core.model_manager import cached_hw, recommended_chain
        from services.llm_service import local_models_in_use, model_display_name

        in_use = {m.phase: m for m in local_models_in_use(settings)}
        chain = recommended_chain(cached_hw())
    except Exception as exc:  # noqa: BLE001 - a report must not fail on a probe
        logger.warning("diagnostics: cannot resolve the model in use (%s)", exc)
        return {}, {}
    main = in_use.get("main")
    return in_use, {
        "model_choice": main.chosen_by if main else "",
        "model_in_catalog": main.in_catalog if main else "",
        "recommended_model": model_display_name(chain[0].name) if chain else "",
    }


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
    summary["per_row_seconds"] = _per_row_seconds(jobs, rows)

    # The last import on its own. The average blends every run of the last
    # twenty, and a test that compares the processor with the graphics card
    # runs the same file twice: averaged, the difference it exists to measure
    # disappears.
    if jobs:
        last = jobs[0]
        last_rows = int(last.n_transactions or 0)
        summary["last_job"] = {
            "rows": last_rows,
            "per_row_seconds": _per_row_seconds([last], last_rows),
        }
    return summary


def _cleaner_seal(session: Any) -> dict[str, int]:
    """What the seal on the description cleaner counted over the last jobs (spec 35, 6).

    A model that invents counterparts looks, in every other section, like one
    that works: the import completes and every movement has a name. The
    refused answers and the retries are the only trace of it. Counts only,
    summed over the last twenty completed jobs; jobs older than the seal
    count as zero.

    Args:
        session: an open database session.
    """
    from core.description_cleaner import SEAL_STAT_KEYS
    from db.models import ImportJob

    totals = {key: 0 for key in SEAL_STAT_KEYS}
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
        return totals
    for job in jobs:
        for key in SEAL_STAT_KEYS:
            totals[key] += int(getattr(job, f"cleaner_{key}", None) or 0)
    return totals


def _per_row_seconds(jobs: list[Any], rows: int) -> dict[str, float]:
    out: dict[str, float] = {}
    if not rows:
        return out
    for phase in _PHASE_COLUMNS:
        total_ms = sum(int(getattr(j, f"ms_{phase}", 0) or 0) for j in jobs)
        if total_ms:
            out[phase] = round(total_ms / 1000.0 / rows, 4)
    return out


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


# Who proposed a movement's category at import, as kept in model_source.
_PROPOSAL_SOURCES = ("llm", "history", "nsi", "rule", "manual", "multi", "fallback")


def _categorization(session: Any) -> dict[str, Any]:
    """Whether the import categorized, and how varied the result is (spec 33).

    Reads the proposal kept at import (model_source, model_category, ...),
    which the user's corrections never change: it describes the product, not
    the user's decisions. Counts only. A category name says nothing a count
    does not, and summed up it describes the person's spending.
    """
    from db.models import Transaction

    out: dict[str, Any] = {
        "movements": 0, "by_source": {}, "fallback": 0, "distinct_categories": 0,
        "distinct_subcategories": 0, "largest_category_share": 0,
        "confidence": {"high": 0, "medium": 0, "low": 0}, "validated": 0,
    }
    try:
        rows = session.query(
            Transaction.model_source, Transaction.model_category,
            Transaction.model_subcategory, Transaction.model_confidence,
            Transaction.human_validated,
        ).all()
    except Exception as exc:  # noqa: BLE001 - a report must not fail on a query
        logger.warning("diagnostics: cannot read the categories (%s)", exc)
        return out

    by_source: dict[str, int] = {}
    per_category: dict[str, int] = {}
    pairs: set[tuple[str, str]] = set()
    for source, category, subcategory, confidence, validated in rows:
        key = "none" if not source else source if source in _PROPOSAL_SOURCES else "other"
        by_source[key] = by_source.get(key, 0) + 1
        if category:
            per_category[category] = per_category.get(category, 0) + 1
            pairs.add((category, subcategory or ""))
        if confidence in out["confidence"]:
            out["confidence"][confidence] += 1
        out["validated"] += int(bool(validated))

    out["movements"] = len(rows)
    out["by_source"] = by_source
    out["fallback"] = by_source.get("fallback", 0)
    out["distinct_categories"] = len(per_category)
    out["distinct_subcategories"] = len(pairs)
    if rows and per_category:
        out["largest_category_share"] = round(max(per_category.values()) / len(rows), 4)
    return out


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
    in_use, model_fields = _model_in_use(settings)

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
            "os_name": rt.get("os_name", ""),
            "os_id": rt.get("os_id", ""),
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
            # Nor does the build alone: with 0 layers on the accelerator the
            # model runs on the processor, and until 2026-10-23 the onboarding
            # wrote exactly that while this field said "yes".
            "acceleration_active": (
                rt["gpu_acceleration_active"]
                and str(settings.get("llama_cpp_n_gpu_layers", "-1")).strip() != "0"
            ),
            "accelerator_available": rt["gpu_acceleration_active"],
            "inference_devices": rt["inference_devices"],
        },
        "inference": {
            "llama_cpp_version": rt["llama_cpp_version"],
            "backend": settings.get("llm_backend", ""),
            "n_ctx": settings.get("llama_cpp_n_ctx", "") or "(auto)",
            "n_gpu_layers": settings.get("llama_cpp_n_gpu_layers", "") or "(auto)",
            "model": _model_name(in_use["main"].path) if "main" in in_use else _model_name(settings.get("llama_cpp_model_path")),
            **model_fields,
            "phases": _phase_settings(settings, in_use),
        },
        "ledger": _counts(session),
        "imports": _import_summary(session),
        "categorization": _categorization(session),
        "description_cleaner": _cleaner_seal(session),
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
                    "imports", "categorization", "description_cleaner", "logs"):
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


def _slug(value: str, limit: int = 40) -> str:
    """Lower case, letters, digits and dots, joined by single dashes."""
    text = re.sub(r"[^a-z0-9.]+", "-", str(value).lower()).strip("-.")
    return text[:limit].rstrip("-.") or "unknown"


def _arch(value: str) -> str:
    """One name per architecture: the same machine reports x86_64 or AMD64."""
    v = str(value).lower()
    if v in ("x86_64", "amd64", "x64"):
        return "amd64"
    if v in ("aarch64", "arm64", "armv8"):
        return "arm64"
    return _slug(v, 16)


def _acceleration(report: dict[str, Any]) -> str:
    """What runs the model: "cpu", or the backend and the card, "vulkan-quadro-p2000"."""
    graphics = report.get("graphics", {})
    if not graphics.get("acceleration_active"):
        return "cpu"
    from core import runtime_info

    devices = [d for d in graphics.get("inference_devices", []) if runtime_info.is_accelerator(d)]
    backend = re.sub(r"\d+$", "", str(devices[0])) if devices else "gpu"
    gpu = graphics.get("gpu", "")
    # Vendor words add length and nothing a reader cannot infer from the model.
    gpu = re.sub(r"(?i)\b(nvidia|amd|ati|intel|corporation|\(r\)|\(tm\))\b", " ", str(gpu))
    parts = [_slug(backend, 12)]
    if gpu.strip() and gpu.strip().lower() != "unknown":
        parts.append(_slug(gpu, 28))
    return "-".join(parts)


def report_filename(report: dict[str, Any]) -> str:
    """A name that says what is inside without opening the file.

    spendifai-report_0.3.1_debian-13_arm64_cpu_20261010-1432.xml

    Version, operating system, architecture, what runs the model and when.
    Several reports from several machines end up in the same folder or
    attached to the same thread, and a name that only carries the version makes
    them indistinguishable. Nothing in it names the person or the machine: no
    host name, no account name.
    """
    app = report.get("application", {})
    system = report.get("system", {})
    stamp = str(report.get("generated_at", ""))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})", stamp)
    when = f"{m[1]}{m[2]}{m[3]}-{m[4]}{m[5]}" if m else "undated"
    parts = [
        "spendifai-report",
        _slug(app.get("version", "unknown"), 20),
        _slug(system.get("os_id") or system.get("os", "unknown"), 24),
        _arch(system.get("arch", "unknown")),
        _acceleration(report),
        when,
    ]
    return "_".join(parts) + ".xml"


def issue_url(report: dict[str, Any], xml: str) -> tuple[str, bool]:
    """The address of the test report form, filled in with this document.

    Returns the address and whether the document fits in it. When it does not,
    the address opens the form with the title only, and the caller tells the
    reader to paste the document, which the page shows in full anyway.
    """
    title = "Test report: " + report_filename(report).removesuffix(".xml")
    base = {"template": ISSUE_TEMPLATE, "title": title}
    compact = re.sub(r">\s+<", "><", xml)
    full = f"{ISSUE_FORM_URL}?{urlencode({**base, 'report': compact})}"
    if len(full) <= _MAX_ISSUE_URL:
        return full, True
    return f"{ISSUE_FORM_URL}?{urlencode(base)}", False


def mail_parts(report: dict[str, Any], intro: str) -> tuple[str, str, str]:
    """Address, subject and body of the email that sends a test report.

    The subject carries the same self-describing name as the file, and the
    body the four steps as a checklist in the format the issue form produces,
    so the saved email can be read into the compatibility table as it is.
    The document itself travels as an attachment: a mail link cannot attach
    a file, and a whole report in the body is past what mail clients accept.
    """
    subject = "Test report: " + report_filename(report).removesuffix(".xml")
    body = intro + "\n\n" + "\n".join(f"- [ ] {label}" for label in TEST_OUTCOMES) + "\n"
    return REPORT_MAIL_ADDRESS, subject, body


def mail_url(report: dict[str, Any], intro: str) -> str:
    """The same email as a mailto link, for a page running in a browser."""
    address, subject, body = mail_parts(report, intro)
    return f"mailto:{address}?subject={quote(subject)}&body={quote(body)}"

