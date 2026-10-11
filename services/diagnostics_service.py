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
SCHEMA_VERSION = "2"

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

    for section in ("application", "system", "graphics", "inference", "ledger", "imports"):
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
    devices = [d for d in graphics.get("inference_devices", []) if str(d).upper() != "CPU"]
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


def mail_url(report: dict[str, Any], intro: str) -> str:
    """A mailto address for sending a test report without a GitHub account.

    The subject carries the same self-describing name as the file, and the
    body the four steps as a checklist in the format the issue form produces,
    so the saved email can be read into the compatibility table as it is.
    The document itself travels as an attachment: a mailto link cannot attach
    a file, and a whole report in the body is past what mail clients accept.
    """
    subject = "Test report: " + report_filename(report).removesuffix(".xml")
    body = intro + "\n\n" + "\n".join(f"- [ ] {label}" for label in TEST_OUTCOMES) + "\n"
    return f"mailto:{REPORT_MAIL_ADDRESS}?subject={quote(subject)}&body={quote(body)}"

