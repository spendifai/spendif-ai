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


def test_a_card_the_settings_keep_off_is_not_acceleration(session, settings, monkeypatch):
    """The library seeing a card is not the model using it.

    With 0 layers on the accelerator the model runs on the processor. The
    report said "active" in that case, and the onboarding wrote 0 on every
    installation until 2026-10-23.
    """
    from core import runtime_info

    monkeypatch.setattr(runtime_info, "_devices", lambda: ["MTL0", "CPU"])
    off = diagnostics.collect(session, {**settings, "llama_cpp_n_gpu_layers": "0"})
    on = diagnostics.collect(session, {**settings, "llama_cpp_n_gpu_layers": "-1"})

    assert off["graphics"]["accelerator_available"] is True
    assert off["graphics"]["acceleration_active"] is False
    assert on["graphics"]["acceleration_active"] is True


def test_logs_are_described_and_not_quoted(session, settings, tmp_path, monkeypatch):
    """The report says whether a trace exists. It does not carry the trace.

    A log line contains absolute paths, and an absolute path on this machine
    contains the account name of whoever runs the application. The whole point
    of naming the log instead of copying it is that asking for the file stays a
    decision the user makes.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app_20260925_090000.log").write_text(
        "2026-09-25 09:00:00 - SPENDIFY - CRITICAL - Unhandled exception\n"
        f"  File \"{SENTINELS['home_path']}\", line 1, in <module>\n"
        f"  processing {SENTINELS['source_file']}\n"
    )
    monkeypatch.setenv("SPENDIFAI_LOG_DIR", str(log_dir))

    report = diagnostics.collect(session, settings)
    xml = diagnostics.to_xml(report)

    # It noticed the crash...
    assert report["logs"]["unhandled_exception_recorded"] is True
    assert report["logs"]["latest_app_log"] == "app_20260925_090000.log"
    assert report["logs"]["app_log_files"] == 1

    # ...without repeating a single line of it.
    for label, value in SENTINELS.items():
        assert value not in xml, f"the log section leaks {label}: {value!r}"
    assert "Traceback" not in xml
    assert "/Users/" not in xml


def _usage(session, **kwargs):
    from db.models import LlmUsageLog

    defaults = dict(
        backend="local_llama_cpp",
        model_id=SENTINELS["home_path"],
        caller="categorizer",
        source_name=SENTINELS["source_file"],
        n_ctx=4096,
        duration_ms=100,
        prompt_tokens=100,
    )
    defaults.update(kwargs)
    session.add(LlmUsageLog(**defaults))
    session.commit()


def test_observed_calls_say_what_happened_without_naming_the_file(session, settings):
    """The call log names the imported file. That column must never be read."""
    _usage(session)
    report = diagnostics.collect(session, settings)
    xml = diagnostics.to_xml(report)

    assert report["llm_observed"], "the call log was not read at all"
    group = report["llm_observed"][0]
    assert group["calls"] == 1
    assert group["phase"] == "categorizer"
    # The model by file name, as everywhere else in this document.
    assert group["model"] == "gemma-3-12b.gguf"

    for label, value in SENTINELS.items():
        assert value not in xml, f"the observed section leaks {label}: {value!r}"


def test_a_prompt_that_fills_the_context_is_reported_as_such(session, settings):
    """The shape of the defect that reported itself as 'all backends failed'.

    A context of 4096 with prompts arriving at 4000 is the one-line version of
    a diagnosis that cost hours on 2026-09-22.
    """
    _usage(session, prompt_tokens=400, duration_ms=50)
    _usage(session, prompt_tokens=4000, duration_ms=90)

    group = diagnostics.collect(session, settings)["llm_observed"][0]

    assert group["calls"] == 2
    assert group["max_prompt_tokens"] == 4000
    assert group["context"] == 4096
    assert group["context_pressure"] is True


def test_a_prompt_well_inside_the_context_is_not_flagged(session, settings):
    _usage(session, prompt_tokens=400)
    _usage(session, prompt_tokens=900)

    group = diagnostics.collect(session, settings)["llm_observed"][0]

    assert group["context_pressure"] is False
    assert group["median_ms"] == 100


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


# ── Whether the import categorized (spec 33) ─────────────────────────────────

CATEGORY_SENTINEL = "Spese mediche di Mario Rossi"


@pytest.fixture()
def categorized_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    proposals = [
        ("llm", CATEGORY_SENTINEL, "Farmacia", "high", True),
        ("llm", CATEGORY_SENTINEL, "Visite", "medium", False),
        ("llm", "Casa", "Affitto", "low", False),
        ("history", CATEGORY_SENTINEL, "Farmacia", "medium", False),
        ("nsi", "Trasporti", "Carburante", "high", False),
        ("fallback", "Altro", "Altro", "low", False),
        (None, None, None, None, False),
        ("someday", "Casa", "Affitto", "high", False),
    ]
    for i, (source, cat, sub, conf, validated) in enumerate(proposals):
        s.add(Transaction(
            id=f"{i:024d}", date="2026-01-31", amount=-1.0, description="x",
            category=cat, subcategory=sub, model_source=source, model_category=cat,
            model_subcategory=sub, model_confidence=conf, human_validated=validated,
        ))
    s.commit()
    yield s
    s.close()


def test_categorization_counts_who_proposed_what(categorized_session, settings):
    c = diagnostics.collect(categorized_session, settings)["categorization"]
    assert c["movements"] == 8
    assert c["by_source"] == {"llm": 3, "history": 1, "nsi": 1, "fallback": 1,
                              "none": 1, "other": 1}
    assert c["fallback"] == 1
    assert c["distinct_categories"] == 4          # medical, home, transport, other
    assert c["distinct_subcategories"] == 5
    assert c["largest_category_share"] == pytest.approx(3 / 8)
    assert c["confidence"] == {"high": 3, "medium": 2, "low": 2}
    assert c["validated"] == 1


def test_categorization_names_no_category(categorized_session, settings):
    xml = diagnostics.to_xml(diagnostics.collect(categorized_session, settings))
    assert "<categorization>" in xml
    for name in (CATEGORY_SENTINEL, "Farmacia", "Trasporti", "Carburante", "Affitto"):
        assert name not in xml, f"the report names a category: {name!r}"


def test_an_empty_ledger_reports_zeros(settings):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    c = diagnostics.collect(s, settings)["categorization"]
    assert c["movements"] == 0
    assert c["largest_category_share"] == 0
    s.close()
