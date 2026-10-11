"""The support report must carry facts about the machine and nothing about the person.

This is the test the diagnostics module points at. It is written with sentinels
rather than by inspecting the report's fields on purpose: a field added later
without anyone thinking about it is exactly the case that needs to fail, and a
test that checks only the fields that exist today would not notice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Account, Base, DocumentSchemaModel, ImportJob, Transaction
from services import diagnostics_service as diagnostics

# Every one of these is a real leak that has a shape. They are deliberately
# unmistakable: if any appears in the document, something copied a value it
# should have counted instead.
SENTINELS = {
    "description": "PAGAMENTO CARTA ESSELUNGA MILANO",
    "account_label": "Conto corrente di Mario Rossi",
    "bank_name": "Banca Popolare di Sondrio",
    "source_file": "revolut_account-statement_2024-02-01_2026-03-16_it-it_57d5a3.csv",
    "source_identifier": "estratto_conto_amex_gennaio.xlsx",
    "home_path": "/Users/mario.rossi/.spendifai/models/gemma-3-12b.gguf",
}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()

    s.add(Account(name=SENTINELS["account_label"], bank_name=SENTINELS["bank_name"]))
    s.add(Transaction(
        id="deadbeefdeadbeefdeadbeef",
        date="2026-01-31",
        amount=-42.5,
        description=SENTINELS["description"],
        source_file=SENTINELS["source_file"],
        account_label=SENTINELS["account_label"],
    ))
    s.add(DocumentSchemaModel(
        source_identifier=SENTINELS["source_identifier"],
        doc_type="bank_account",
    ))
    s.add(ImportJob(status="completed", n_transactions=120, n_files=3,
                    ms_header_detection=2400, ms_footer_detection=1200))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def settings():
    # A model path is the usual way an account name reaches a document that was
    # supposed to be anonymous.
    return {
        "llm_backend": "local_llama_cpp",
        "llama_cpp_model_path": SENTINELS["home_path"],
        "llama_cpp_n_ctx": "4096",
        "llama_cpp_n_gpu_layers": "0",
    }


def test_report_carries_no_personal_data(session, settings):
    xml = diagnostics.to_xml(diagnostics.collect(session, settings))
    for label, value in SENTINELS.items():
        assert value not in xml, f"the report leaks {label}: {value!r}"
    # The user account name on its own, not only the whole path.
    assert "mario.rossi" not in xml


def test_model_appears_by_file_name_only(session, settings):
    xml = diagnostics.to_xml(diagnostics.collect(session, settings))
    # Knowing which model is running is the point; knowing whose home it sits
    # in is the leak.
    assert "gemma-3-12b.gguf" in xml
    assert "/Users/" not in xml


def test_counts_are_reported_without_the_rows_behind_them(session, settings):
    report = diagnostics.collect(session, settings)
    assert report["ledger"]["transactions"] == 1
    assert report["ledger"]["accounts"] == 1
    assert report["ledger"]["saved_formats"] == 1


def test_seconds_per_row_come_from_the_persisted_timings(session, settings):
    report = diagnostics.collect(session, settings)
    per_row = report["imports"]["per_row_seconds"]
    # 2400 ms over 120 rows = 0.02 s per row. The number a reader needs in
    # order to tell a usable machine from an unusable one.
    assert per_row["header_detection"] == pytest.approx(0.02)
    assert per_row["footer_detection"] == pytest.approx(0.01)
    assert report["imports"]["files"] == 3
    assert report["imports"]["rows"] == 120


def test_rating_is_marked_as_declared_and_absent_when_not_given(session, settings):
    report = diagnostics.collect(session, settings)
    assert "user_rating" not in diagnostics.to_xml(report)

    xml = diagnostics.to_xml(report, stars=5)
    # It is the only value in the document the machine did not measure, so it
    # has to be told apart from the ones it did.
    assert 'stars="5"' in xml
    assert 'declared_by="user"' in xml


def test_schema_version_is_declared(session, settings):
    xml = diagnostics.to_xml(diagnostics.collect(session, settings))
    assert f'schema="{diagnostics.SCHEMA_VERSION}"' in xml


def test_acceleration_is_reported_as_its_own_answer(session, settings):
    report = diagnostics.collect(session, settings)
    graphics = report["graphics"]
    # A card being present and a card being used are different facts, and the
    # report has to answer the second one rather than let a reader infer it
    # from the first.
    assert "acceleration_active" in graphics
    assert isinstance(graphics["acceleration_active"], bool)


def _report(**over):
    """A report shaped like collect() output, with only what the name reads."""
    base = {
        "generated_at": "2026-10-10 14:32:07 UTC",
        "application": {"version": "0.3.1"},
        "system": {"os": "Linux", "os_id": "debian-13", "arch": "aarch64"},
        "graphics": {"gpu": "unknown", "acceleration_active": False,
                     "inference_devices": ["CPU"]},
    }
    for key, value in over.items():
        base[key] = {**base[key], **value}
    return base


def test_file_name_says_what_is_inside():
    # Several reports from several machines land in one folder or one thread:
    # the name alone has to tell them apart.
    assert diagnostics.report_filename(_report()) == (
        "spendifai-report_0.3.1_debian-13_arm64_cpu_20261010-1432.xml"
    )


def test_file_name_names_the_backend_and_the_card_when_accelerated():
    name = diagnostics.report_filename(_report(
        system={"os_id": "arch", "arch": "x86_64"},
        graphics={"gpu": "AMD Radeon RX 6700 XT", "acceleration_active": True,
                  "inference_devices": ["Vulkan0", "CPU"]},
    ))
    assert name == "spendifai-report_0.3.1_arch_amd64_vulkan-radeon-rx-6700-xt_20261010-1432.xml"


def test_file_name_carries_nothing_personal(session, settings):
    name = diagnostics.report_filename(diagnostics.collect(session, settings))
    for label, value in SENTINELS.items():
        assert value not in name, f"the file name leaks {label}"
    assert "mario" not in name
    assert name.startswith("spendifai-report_") and name.endswith(".xml")
    assert all(c.isalnum() or c in "._-" for c in name)


def test_last_import_is_reported_apart_from_the_average(session, settings):
    session.add(ImportJob(status="completed", n_transactions=100, n_files=1,
                          ms_categorizing=5000))
    session.commit()
    imports = diagnostics.collect(session, settings)["imports"]
    # The average blends both runs; the last one alone is what a CPU against
    # GPU comparison reads.
    assert imports["last_job"]["rows"] == 100
    assert imports["last_job"]["per_row_seconds"] == {"categorizing": pytest.approx(0.05)}
    assert "header_detection" in imports["per_row_seconds"]
    assert "<last_job>" in diagnostics.to_xml(diagnostics.collect(session, settings))


def test_issue_link_carries_the_document_when_it_fits():
    report = _report()
    url, fits = diagnostics.issue_url(report, "<spendifai_report schema=\"2\">\n  <a>1</a>\n</spendifai_report>")
    assert fits
    assert url.startswith(diagnostics.ISSUE_FORM_URL)
    assert "template=test_report.yml" in url
    assert "report=" in url
    assert "debian-13_arm64_cpu" in url


def test_issue_link_drops_the_document_rather_than_break():
    url, fits = diagnostics.issue_url(_report(), "<x>" + "a" * 20000 + "</x>")
    assert not fits
    assert "report=" not in url
    assert len(url) < 1000
