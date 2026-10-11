#!/usr/bin/env python3
"""Turn a test report filed as an issue into a row of the compatibility table.

WHY IT EXISTS
    A tester runs the application, opens the Diagnostics page and sends the
    report through the issue form. Without this, someone reads each report and
    copies operating system, card, outcome and timings into a table by hand,
    which is exactly the work that stops being done after the fifth report.

WHAT IT TRUSTS, AND WHAT NOT
    The issue form is public: anyone can open one, and the body is whatever
    they typed. It is therefore read as untrusted data from start to finish:
      - the XML is refused if it declares a DOCTYPE or an entity, and above a
        size no real report reaches;
      - only named fields are read, each one capped in length, and nothing
        walks the document;
      - every value that reaches the table is reduced to characters that
        cannot form a link, an image or HTML in Markdown.
    The workflow that runs this never puts the body on a command line, and the
    result reaches the repository only through a pull request someone merges.

STANDARD LIBRARY ONLY
    It runs in a workflow without installing the project, so it imports
    nothing outside Python itself.

USAGE
    python scripts/compat_report.py --event "$GITHUB_EVENT_PATH" \\
        --store docs/compatibility/reports.jsonl --docs docs
    python scripts/compat_report.py --mail report.eml \\
        --store docs/compatibility/reports.jsonl --docs docs
    python scripts/compat_report.py --xml report.xml --outcomes installed,started \\
        --store docs/compatibility/reports.jsonl --docs docs
    python scripts/compat_report.py --store docs/compatibility/reports.jsonl --docs docs --render-only

REPORTS THAT ARRIVE BY EMAIL
    Not every tester has a GitHub account, so the Diagnostics page also offers
    an email with the document attached and the four outcomes as a checklist
    in the body. Saved as an .eml file, that email goes through --mail: the
    same parser, the same refusals, the same row. Nobody copies a value from
    an email into the table, which is the work this script exists to remove.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 64 * 1024
# A saved email carries headers and, sometimes, a second encoding of the body.
MAX_MAIL_BYTES = 1024 * 1024
MAX_FIELD = 80
KNOWN_SCHEMAS = {"1", "2"}

# Checkbox labels in .github/ISSUE_TEMPLATE/test_report.yml, in order.
OUTCOMES = (
    ("installed", "Installed"),
    ("started", "Started from the application menu or icon"),
    ("imported", "Imported a statement"),
    ("uninstalled", "Uninstalled cleanly"),
)


class ReportError(ValueError):
    """The issue does not carry a report this script can read."""


# --------------------------------------------------------------------------
# Reading the issue
# --------------------------------------------------------------------------

def extract_xml(body: str) -> str:
    """The report from the issue body: the fenced block the form produces."""
    m = re.search(r"```(?:xml)?\s*\n(.*?)\n\s*```", body or "", re.DOTALL)
    text = (m.group(1) if m else body or "").strip()
    start = text.find("<")
    if start < 0:
        raise ReportError("no XML document in the issue")
    return text[start:]


def extract_outcomes(body: str) -> dict[str, bool]:
    ticked = {
        line.split("]", 1)[1].strip()
        for line in (body or "").splitlines()
        # A reply quotes the list ("> - [x] ..."), so quote marks are allowed.
        if re.match(r"\s*(?:>\s*)*-\s*\[[xX]\]", line)
    }
    return {key: label in ticked for key, label in OUTCOMES}


def _clean(value: object, limit: int = MAX_FIELD) -> str:
    """Letters, digits and a few separators: nothing Markdown can turn into markup.

    No brackets, so no link or image; no angle brackets, so no HTML; no colon,
    so no "https://" for GitHub to turn into a link on its own.
    """
    text = re.sub(r"[^\w .,;+/()@-]", " ", str(value or ""), flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _number(value: str | None) -> float | None:
    try:
        return round(float(value), 4) if value not in (None, "") else None
    except ValueError:
        return None


def parse_report(xml: str) -> dict[str, object]:
    """The fields the table needs, read one by one from the report."""
    if len(xml.encode("utf-8")) > MAX_XML_BYTES:
        raise ReportError("report larger than any real one")
    if re.search(r"<!(DOCTYPE|ENTITY)", xml, re.IGNORECASE):
        raise ReportError("report declares a DOCTYPE or an entity")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ReportError(f"report is not well-formed XML ({exc})") from exc
    if root.tag != "spendifai_report":
        raise ReportError(f"unexpected document: <{_clean(root.tag, 40)}>")
    schema = root.get("schema", "")
    if schema not in KNOWN_SCHEMAS:
        raise ReportError(f"unknown report schema {_clean(schema, 10)!r}")

    def text(path: str) -> str:
        node = root.find(path)
        return _clean(node.text if node is not None else "")

    devices = [_clean(n.text, 20) for n in root.findall("graphics/inference_devices/item")][:8]
    # Seconds per row of the slowest phase, categorizing: the last import when
    # the report has it, the average otherwise.
    per_row = _number(text("imports/last_job/per_row_seconds/categorizing")) \
        or _number(text("imports/per_row_seconds/categorizing"))
    rating = root.find("user_rating")

    return {
        "schema": schema,
        "generated_at": _clean(root.get("generated_at", ""), 30),
        "version": text("application/version"),
        "install_method": text("application/install_method"),
        "os_name": text("system/os_name") or text("system/os_version"),
        "os_id": text("system/os_id") or text("system/os"),
        "arch": text("system/arch"),
        "ram_gb": _number(text("system/ram_gb")),
        "gpu": text("graphics/gpu"),
        "acceleration": text("graphics/acceleration_active") == "True",
        "devices": devices,
        "llama_cpp_version": text("inference/llama_cpp_version"),
        "model": text("inference/model"),
        "categorizing_s_per_row": per_row,
        "stars": _number(rating.get("stars")) if rating is not None else None,
    }


def row_from_event(event: dict) -> dict[str, object]:
    issue = event.get("issue") or {}
    body = issue.get("body") or ""
    row = parse_report(extract_xml(body))
    row["outcome"] = extract_outcomes(body)
    row["issue"] = int(issue.get("number") or 0)
    return row


def row_from_mail(raw: bytes) -> dict[str, object]:
    """A row from a saved email: the XML attached, the outcomes in the body.

    The attachment is preferred; a document pasted into the body is read as
    the issue form would read it. The email is as untrusted as an issue body.
    """
    if len(raw) > MAX_MAIL_BYTES:
        raise ReportError("email larger than any real report")
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    attached, body = None, ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = (part.get_filename() or "").lower()
        ctype = part.get_content_type()
        if attached is None and (name.endswith(".xml") or ctype in ("application/xml", "text/xml")):
            attached = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        elif ctype == "text/plain" and not body:
            body = part.get_content()
    xml = attached.strip() if attached else extract_xml(body)
    row = parse_report(xml[xml.find("<"):] if "<" in xml else xml)
    row["outcome"] = extract_outcomes(body)
    row["issue"] = 0
    row["source"] = "email"
    return row


def row_from_xml(xml: str, outcomes: str) -> dict[str, object]:
    """A row from a bare document, with the outcomes named on the command line."""
    named = {o.strip() for o in (outcomes or "").split(",") if o.strip()}
    unknown = named - {key for key, _ in OUTCOMES}
    if unknown:
        raise ReportError(f"unknown outcomes: {', '.join(sorted(unknown))}")
    row = parse_report(xml.strip())
    row["outcome"] = {key: key in named for key, _ in OUTCOMES}
    row["issue"] = 0
    row["source"] = "email"
    return row


# --------------------------------------------------------------------------
# Store and table
# --------------------------------------------------------------------------

def load_store(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_store(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r.get("issue", 0))
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def _identity(row: dict) -> tuple:
    """What makes two rows the same report.

    An issue is one report however often it is edited. An email has no
    number, so the moment the document was generated and the machine stand
    in for one: the same email processed twice is the same row.
    """
    if row.get("issue"):
        return ("issue", row["issue"])
    return ("email", row.get("generated_at"), row.get("os_id"), row.get("arch"))


def upsert(rows: list[dict], row: dict) -> list[dict]:
    """One row per report: an edited issue replaces what it said before."""
    return [r for r in rows if _identity(r) != _identity(row)] + [row]


def _arch(value: str) -> str:
    v = str(value).lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(v, v)


def _runs_on(row: dict) -> str:
    if not row.get("acceleration"):
        return "CPU"
    accel = [d for d in row.get("devices", []) if d.upper() != "CPU"]
    return re.sub(r"\d+$", "", accel[0]) if accel else "GPU"


def _newer(a: dict, b: dict) -> bool:
    return (a.get("generated_at", ""), a.get("issue", 0)) > (b.get("generated_at", ""), b.get("issue", 0))


def _reference(row: dict) -> str:
    return f"#{int(row['issue'])}" if row.get("issue") else "email"


def latest_per_configuration(rows: list[dict]) -> list[dict]:
    """The newest report for each machine configuration, newest first."""
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("os_id"), _arch(r.get("arch", "")), r.get("gpu"),
               r.get("install_method"), _runs_on(r))
        if key not in best or _newer(r, best[key]):
            best[key] = r
    return sorted(best.values(), key=lambda r: (r.get("os_id", ""), _arch(r.get("arch", ""))))


# --------------------------------------------------------------------------
# Test matrix
# --------------------------------------------------------------------------
#
# The configurations we mean to cover, from docs/compatibility/matrix.json,
# each ticked by the reports that match it. The tick is computed, never
# typed: a configuration is done when a report from it says all four steps
# went through, and partly done when a report exists but some did not.

DONE, PARTIAL, OPEN = "\u2705", "\u25d0", "\u2b1c"


def load_matrix(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _matches(entry: dict, row: dict) -> bool:
    if str(row.get("os_id", "")).lower() != str(entry.get("os", "")).lower():
        return False
    if _arch(row.get("arch", "")) != entry.get("arch"):
        return False
    vendors = [v.lower() for v in entry.get("gpu", [])]
    return not vendors or any(v in str(row.get("gpu", "")).lower() for v in vendors)


def _complete(row: dict) -> bool:
    outcome = row.get("outcome", {})
    return all(outcome.get(key) for key, _ in OUTCOMES)


def matrix_status(entry: dict, rows: list[dict]) -> tuple[str, dict | None]:
    """The tick for one configuration, and the report it rests on."""
    found = [r for r in rows if _matches(entry, r)]
    if not found:
        return OPEN, None
    complete = [r for r in found if _complete(r)]
    pool = complete or found
    newest = pool[0]
    for r in pool[1:]:
        if _newer(r, newest):
            newest = r
    return (DONE if complete else PARTIAL), newest


def render_matrix(matrix: list[dict], rows: list[dict], lang: str) -> list[str]:
    t = TEXT[lang]
    if not matrix:
        return []
    out = [f"## {t['matrix_title']}", "", t["matrix_intro"], "",
           "| " + " | ".join(t["matrix_cols"]) + " |", "|" + "---|" * len(t["matrix_cols"])]
    for entry in matrix:
        mark, row = matrix_status(entry, rows)
        out.append("| " + " | ".join([
            mark,
            _clean(entry.get("label")),
            _clean(entry.get("arch")),
            _clean(entry.get("package")),
            _runs_on(row) if row else "-",
            _clean(row.get("version")) if row else "-",
            _reference(row) if row else "-",
        ]) + " |")
    return out + ["", f"## {t['reports_title']}", ""]


TEXT = {
    "en": {
        "title": "Compatibility",
        "intro": ("Machines Spendif.ai has been installed on, one row per configuration, newest report "
                  "first. Generated from the test reports filed through the issue form or by email: do not edit by hand."),
        "how": ("To add yours: Diagnostics page, then **Send as a test report on GitHub**; without a GitHub "
                "account, **Save the document** and send it to support@spendif.ai with the email link on "
                "the same page."),
        "cols": ["Version", "Operating system", "Arch", "Graphics card", "Runs on", "Installed with",
                 "Install", "Start", "Import", "Uninstall", "s/row (categorizing)", "Report"],
        "empty": "No reports yet.",
        "yes": "yes", "no": "-",
        "matrix_title": "Test matrix",
        "matrix_intro": ("The configurations we test before a release. \u2705 a report says all four steps "
                         "went through, \u25d0 a report arrived but some step did not, \u2b1c no report yet. "
                         "Download and launch commands: [test_builds.md](test_builds.md)."),
        "matrix_cols": ["", "Configuration", "Arch", "Package", "Runs on", "Version", "Report"],
        "reports_title": "All reports",
    },
    "it": {
        "title": "Compatibilita'",
        "intro": ("I computer su cui Spendif.ai e' stato installato, una riga per configurazione, il resoconto "
                  "piu' recente per primo. Generata dai resoconti di prova inviati con il modulo delle issue o via email: "
                  "non modificarla a mano."),
        "how": ("Per aggiungere il tuo: pagina Diagnostica, poi **Invia come resoconto di prova su "
                "GitHub**; senza un account GitHub, **Salva il documento** e mandalo a support@spendif.ai con "
                "il link email della stessa pagina."),
        "cols": ["Versione", "Sistema operativo", "Arch", "Scheda grafica", "Gira su", "Installato con",
                 "Installa", "Avvio", "Import", "Disinstalla", "s/riga (categorie)", "Resoconto"],
        "empty": "Ancora nessun resoconto.",
        "yes": "si'", "no": "-",
        "matrix_title": "Matrice di prova",
        "matrix_intro": ("Le configurazioni che proviamo prima di una release. \u2705 un resoconto dice che "
                         "tutti e quattro i passi sono riusciti, \u25d0 un resoconto e' arrivato ma qualche "
                         "passo no, \u2b1c ancora nessun resoconto. Comandi per scaricare e avviare: "
                         "[test_builds.it.md](test_builds.it.md)."),
        "matrix_cols": ["", "Configurazione", "Arch", "Pacchetto", "Gira su", "Versione", "Resoconto"],
        "reports_title": "Tutti i resoconti",
    },
}


def render(rows: list[dict], lang: str, matrix: list[dict] | None = None) -> str:
    t = TEXT[lang]
    out = [f"# {t['title']}", "", t["intro"], "", t["how"], ""]
    out += render_matrix(matrix or [], rows, lang)
    table = latest_per_configuration(rows)
    if not table:
        return "\n".join(out + [t["empty"], ""])
    out.append("| " + " | ".join(t["cols"]) + " |")
    out.append("|" + "---|" * len(t["cols"]))
    for r in table:
        outcome = r.get("outcome", {})
        per_row = r.get("categorizing_s_per_row")
        out.append("| " + " | ".join([
            _clean(r.get("version")),
            _clean(r.get("os_name")),
            _clean(_arch(r.get("arch", ""))),
            _clean(r.get("gpu")) or "-",
            _runs_on(r),
            _clean(r.get("install_method")),
            *(t["yes"] if outcome.get(k) else t["no"] for k, _ in OUTCOMES),
            f"{per_row:.3f}" if isinstance(per_row, (int, float)) else "-",
            _reference(r),
        ]) + " |")
    return "\n".join(out + [""])


def write_docs(rows: list[dict], docs: Path, matrix: list[dict] | None = None) -> None:
    (docs / "compatibility.md").write_text(render(rows, "en", matrix), encoding="utf-8")
    (docs / "compatibility.it.md").write_text(render(rows, "it", matrix), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--event", type=Path, help="GitHub event payload (issues)")
    source.add_argument("--mail", type=Path, help="a report that arrived by email, saved as .eml")
    source.add_argument("--xml", type=Path, help="a bare report document")
    ap.add_argument("--outcomes", default="",
                    help="with --xml: the steps that went through, e.g. installed,started,imported,uninstalled")
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--docs", type=Path, required=True)
    ap.add_argument("--matrix", type=Path,
                    help="configurations to tick (default: matrix.json next to the store)")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--summary", type=Path, help="write a one-line summary here for the issue comment")
    args = ap.parse_args(argv)

    rows = load_store(args.store)
    matrix = load_matrix(args.matrix or args.store.parent / "matrix.json")
    if not args.render_only:
        if not (args.event or args.mail or args.xml):
            ap.error("--event, --mail or --xml is required unless --render-only")
        try:
            if args.event:
                row = row_from_event(json.loads(args.event.read_text(encoding="utf-8")))
            elif args.mail:
                row = row_from_mail(args.mail.read_bytes())
            else:
                row = row_from_xml(args.xml.read_text(encoding="utf-8", errors="replace"), args.outcomes)
        except ReportError as exc:
            if args.summary:
                args.summary.write_text(f"error: {exc}\n", encoding="utf-8")
            print(f"not a readable report: {exc}", file=sys.stderr)
            return 2
        rows = upsert(rows, row)
        save_store(args.store, rows)
        line = (f"{row['version']} | {row['os_name']} | {_arch(row['arch'])} | "
                f"{row['gpu'] or '-'} | {_runs_on(row)} | {row['install_method']}")
        if args.summary:
            args.summary.write_text(line + "\n", encoding="utf-8")
        else:
            print(f"added: {line}")
    write_docs(rows, args.docs, matrix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
