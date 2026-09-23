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
