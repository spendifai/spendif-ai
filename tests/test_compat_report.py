"""The compatibility table is built from issue bodies anyone can write.

These tests feed the parser a report exactly as the Diagnostics page produces
it, wrapped the way the GitHub issue form wraps it, and then the shapes a
hostile or careless body can take.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from services import diagnostics_service as diagnostics

_SPEC = importlib.util.spec_from_file_location(
    "compat_report", Path(__file__).resolve().parent.parent / "scripts" / "compat_report.py"
)
compat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compat)


def _report(**over) -> dict:
    report = {
        "generated_at": "2026-10-10 14:32:07 UTC",
        "application": {"version": "0.3.1", "build_time": "2026-10-09 10:00",
                        "install_method": "deb", "packaged_build": True},
        "system": {"os": "Linux", "os_name": "Debian GNU/Linux 13 (trixie)", "os_id": "debian-13",
                   "os_version": "Linux-6.12-aarch64-with-glibc2.41", "arch": "aarch64",
                   "ram_gb": 8, "emulated_x64_on_arm": False, "python_version": "3.13.7"},
        "graphics": {"gpu": "unknown", "vram_gb": 0, "gpu_cores": 0,
                     "acceleration_active": False, "inference_devices": ["CPU"]},
        "inference": {"llama_cpp_version": "0.3.35", "backend": "local_llama_cpp",
                      "n_ctx": "(auto)", "n_gpu_layers": "(auto)", "model": "qwen.gguf", "phases": {}},
        "ledger": {"transactions": 10},
        "imports": {"jobs": 2, "files": 2, "rows": 200,
                    "per_row_seconds": {"categorizing": 0.5},
                    "last_job": {"rows": 100, "per_row_seconds": {"categorizing": 0.25}}},
    }
    for key, value in over.items():
        report[key] = {**report[key], **value}
    return report


def _issue_body(xml: str, ticked=("Installed", "Imported a statement")) -> str:
    boxes = "\n".join(
        f"- [{'X' if label in ticked else ' '}] {label}" for _, label in compat.OUTCOMES
    )
    return f"### Report\n\n```xml\n{xml}\n```\n\n### Outcome\n\n{boxes}\n\n### Notes\n\n_No response_\n"


def _event(body: str, number: int = 7) -> dict:
    return {"issue": {"number": number, "body": body}}


def test_a_real_report_becomes_a_row():
    xml = diagnostics.to_xml(_report(), stars=4)
    row = compat.row_from_event(_event(_issue_body(xml)))
    assert row["os_name"] == "Debian GNU/Linux 13 (trixie)"
    assert row["arch"] == "aarch64"
    assert row["install_method"] == "deb"
    assert row["acceleration"] is False
    # The last import, not the average: that is the number a comparison reads.
    assert row["categorizing_s_per_row"] == pytest.approx(0.25)
    assert row["outcome"] == {"installed": True, "started": False,
                              "imported": True, "uninstalled": False}
    assert row["stars"] == 4
    assert row["issue"] == 7


def test_schema_1_reports_still_read():
    xml = diagnostics.to_xml(_report()).replace('schema="2"', 'schema="1"')
    xml = xml.replace("<os_name>Debian GNU/Linux 13 (trixie)</os_name>", "")
    row = compat.parse_report(compat.extract_xml(_issue_body(xml)))
    assert row["os_name"].startswith("Linux-6.12")


@pytest.mark.parametrize("xml", [
    '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><spendifai_report schema="2"/>',
    '<other schema="2"/>',
    '<spendifai_report schema="99"/>',
    '<spendifai_report schema="2"><unclosed></spendifai_report>',
    "<spendifai_report schema=\"2\">" + "a" * (70 * 1024) + "</spendifai_report>",
], ids=["doctype-entity", "other-root", "unknown-schema", "malformed", "oversized"])
def test_unreadable_or_hostile_reports_are_refused(xml):
    with pytest.raises(compat.ReportError):
        compat.parse_report(xml)


def test_body_without_xml_is_refused():
    with pytest.raises(compat.ReportError):
        compat.row_from_event(_event("just some text"))


def test_values_cannot_inject_markdown():
    xml = diagnostics.to_xml(_report(graphics={
        "gpu": "![x](https://evil.example/p.png) <img src=x> | [link](javascript:1)",
        "acceleration_active": True, "inference_devices": ["Vulkan0", "CPU"],
    }))
    row = compat.row_from_event(_event(_issue_body(xml)))
    table = compat.render([row], "en")
    for bad in ("![", "](", "<img", "https://", "javascript:", "| [link"):
        assert bad not in table


def test_one_row_per_configuration_newest_wins(tmp_path):
    old = compat.row_from_event(_event(_issue_body(diagnostics.to_xml(
        _report(application={"version": "0.3.0"}, imports={}) | {"generated_at": "2026-09-01 10:00:00 UTC"}
    )), number=1))
    new = compat.row_from_event(_event(_issue_body(diagnostics.to_xml(_report())), number=2))
    rows = compat.upsert(compat.upsert([], old), new)
    table = compat.render(rows, "en")
    assert "0.3.1" in table and "0.3.0" not in table
    assert "#2" in table


def test_edited_issue_replaces_its_row(tmp_path):
    store = tmp_path / "reports.jsonl"
    body = _issue_body(diagnostics.to_xml(_report()))
    for ticked in ((), ("Installed",)):
        event = tmp_path / "event.json"
        event.write_text(json.dumps(_event(_issue_body(diagnostics.to_xml(_report()), ticked))))
        assert compat.main(["--event", str(event), "--store", str(store), "--docs", str(tmp_path)]) == 0
    rows = compat.load_store(store)
    assert len(rows) == 1 and rows[0]["outcome"]["installed"] is True
    assert (tmp_path / "compatibility.md").exists()
    assert (tmp_path / "compatibility.it.md").exists()
    assert body  # the body itself is never stored, only the parsed row
    assert "```" not in store.read_text()


def test_unreadable_issue_exits_with_a_summary(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(_event("no report here")))
    summary = tmp_path / "summary.txt"
    code = compat.main(["--event", str(event), "--store", str(tmp_path / "s.jsonl"),
                        "--docs", str(tmp_path), "--summary", str(summary)])
    assert code == 2
    assert summary.read_text().startswith("error:")


# --------------------------------------------------------------------------
# Reports that arrive by email
# --------------------------------------------------------------------------

def _mail(xml: str | None, ticked=("Installed", "Started from the application menu or icon"),
          quoted: bool = False) -> bytes:
    """An email as a mail client saves it: checklist in the body, document attached."""
    from email.message import EmailMessage
    from urllib.parse import parse_qs, urlparse

    url = diagnostics.mail_url(_report(), "Attach the saved document and tick each step:")
    body = parse_qs(urlparse(url).query)["body"][0]
    for label in ticked:
        body = body.replace(f"- [ ] {label}", f"- [x] {label}")
    if quoted:
        body = "\n".join("> " + line for line in body.splitlines())
    msg = EmailMessage()
    msg["Subject"] = parse_qs(urlparse(url).query)["subject"][0]
    msg["To"] = diagnostics.REPORT_MAIL_ADDRESS
    msg.set_content(body)
    if xml is not None:
        msg.add_attachment(xml.encode("utf-8"), maintype="application", subtype="xml",
                           filename="spendifai-report.xml")
    return msg.as_bytes()


def test_the_checklist_matches_the_form_and_the_script():
    """Three places name the four steps; a reworded one would never be ticked."""
    template = (Path(__file__).resolve().parent.parent
                / ".github" / "ISSUE_TEMPLATE" / "test_report.yml").read_text(encoding="utf-8")
    labels = tuple(label for _, label in compat.OUTCOMES)

    assert diagnostics.TEST_OUTCOMES == labels
    for label in labels:
        assert f"label: {label}" in template, label


def test_an_emailed_report_becomes_a_row():
    row = compat.row_from_mail(_mail(diagnostics.to_xml(_report())))

    assert row["os_id"] == "debian-13"
    assert row["source"] == "email"
    assert row["outcome"] == {"installed": True, "started": True,
                              "imported": False, "uninstalled": False}
    assert compat._reference(row) == "email"


def test_a_reply_that_quotes_the_checklist_still_counts():
    ticked = tuple(label for _, label in compat.OUTCOMES)
    row = compat.row_from_mail(_mail(diagnostics.to_xml(_report()), ticked=ticked, quoted=True))

    assert all(row["outcome"].values())


def test_the_same_email_twice_is_one_row(tmp_path):
    raw = _mail(diagnostics.to_xml(_report()))
    rows = compat.upsert([], compat.row_from_mail(raw))
    rows = compat.upsert(rows, compat.row_from_mail(raw))

    assert len(rows) == 1


@pytest.mark.parametrize("xml", [
    None,
    '<!DOCTYPE x [<!ENTITY a "b">]><spendifai_report schema="2"/>',
], ids=["no-attachment", "entity"])
def test_an_email_without_a_clean_report_is_refused(xml):
    with pytest.raises(compat.ReportError):
        compat.row_from_mail(_mail(xml))


def test_a_bare_document_takes_its_outcomes_from_the_command_line(tmp_path):
    path = tmp_path / "r.xml"
    path.write_text(diagnostics.to_xml(_report()), encoding="utf-8")
    store, docs = tmp_path / "reports.jsonl", tmp_path

    assert compat.main(["--xml", str(path), "--outcomes", "installed,started,imported,uninstalled",
                        "--store", str(store), "--docs", str(docs)]) == 0
    [row] = compat.load_store(store)
    assert all(row["outcome"].values())

    with pytest.raises(compat.ReportError):
        compat.row_from_xml(path.read_text(encoding="utf-8"), "installed,flew")


# --------------------------------------------------------------------------
# Test matrix
# --------------------------------------------------------------------------

def _row(os_id="windows-11", arch="AMD64", gpu="NVIDIA Quadro P4000", done=True, issue=1):
    outcome = {key: done for key, _ in compat.OUTCOMES}
    if not done:
        outcome["installed"] = True
    return {"os_id": os_id, "arch": arch, "gpu": gpu, "outcome": outcome, "issue": issue,
            "generated_at": "2026-10-10 10:00:00 UTC", "version": "0.3.1+gabc1234",
            "acceleration": True, "devices": ["Vulkan0", "CPU"]}


def test_a_configuration_is_ticked_only_by_a_matching_complete_report():
    nvidia = {"label": "Windows 11, NVIDIA", "os": "windows-11", "arch": "amd64",
              "package": "bundle", "gpu": ["nvidia"]}
    amd = {**nvidia, "label": "Windows 11, AMD", "gpu": ["amd", "radeon"]}

    assert compat.matrix_status(nvidia, [])[0] == compat.OPEN
    assert compat.matrix_status(nvidia, [_row(done=False)])[0] == compat.PARTIAL
    assert compat.matrix_status(nvidia, [_row(done=False), _row(issue=2)])[0] == compat.DONE
    # An NVIDIA report says nothing about the AMD driver.
    assert compat.matrix_status(amd, [_row()])[0] == compat.OPEN
    # Nor does a report from another architecture.
    assert compat.matrix_status(nvidia, [_row(arch="ARM64")])[0] == compat.OPEN


def test_the_matrix_is_rendered_from_the_file_next_to_the_store(tmp_path):
    store = tmp_path / "compatibility" / "reports.jsonl"
    store.parent.mkdir()
    (store.parent / "matrix.json").write_text(json.dumps([
        {"label": "Windows 11, NVIDIA", "os": "windows-11", "arch": "amd64",
         "package": "bundle", "gpu": ["nvidia"]},
        {"label": "Fedora 42", "os": "fedora-42", "arch": "arm64", "package": "bundle"},
    ]), encoding="utf-8")
    compat.save_store(store, [_row(issue=9)])

    assert compat.main(["--store", str(store), "--docs", str(tmp_path), "--render-only"]) == 0
    text = (tmp_path / "compatibility.md").read_text(encoding="utf-8")
    assert f"| {compat.DONE} | Windows 11, NVIDIA | amd64 | bundle | Vulkan | 0.3.1+gabc1234 | #9 |" in text
    assert f"| {compat.OPEN} | Fedora 42 | arm64 | bundle | - | - | - |" in text
