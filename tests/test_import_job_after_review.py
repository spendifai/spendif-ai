"""A job that paused for a schema review stops saying "waiting" once confirmed.

The pending reviews live in the session, the jobs in the database. Confirming
used to update only the last job that had paused, leaving the earlier ones on
"waiting for review" for good, and never added the confirmed movements to the
job's count. Synthetic data only.
"""

from __future__ import annotations

from sqlalchemy import create_engine

from db.models import Base
from services.import_service import ImportService


def _engine(path):
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


def test_movements_confirmed_later_add_to_the_job(tmp_path):
    svc = ImportService(_engine(tmp_path / "ledger.db"))
    job = svc.create_job(n_files=2)
    svc.update_job(job.id, n_transactions=66)

    assert svc.add_job_transactions(job.id, 35) == 101
    assert svc.add_job_transactions(9999, 5) is None


# ── The review form, executed in a real Streamlit runtime ────────────────────

def _review_script(db_path: str, job_ids: list) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    from types import SimpleNamespace

    import streamlit as st
    from sqlalchemy import create_engine

    from core.models import Confidence, DocumentType, SignConvention
    from core.schemas import DocumentSchema
    from services.import_service import ImportService
    from ui import upload_page

    class _Service(ImportService):
        def get_raw_head(self, *a, **k):
            raise RuntimeError("no preview in this test")

        def get_normalized_preview(self, *a, **k):
            return []

        def process_file_single(self, *a, **k):
            return SimpleNamespace(transactions=[object()] * 35, skipped_count=0)

        def persist_result(self, result):
            pass

    schema = DocumentSchema(
        doc_type=DocumentType("bank_account"),
        date_col="Data", amount_col="Importo", description_col="Descrizione",
        sign_convention=SignConvention("signed_single"),
        date_format="%d/%m/%Y", account_label="Conto", confidence=Confidence.medium,
    )
    st.session_state.setdefault("_pending_schema_reviews", [
        {
            "filename": f"estratto_{jid}.xlsx",
            "raw_bytes": b"",
            "job_id": jid,
            "result": SimpleNamespace(
                doc_schema=schema,
                available_columns=["Data", "Importo", "Descrizione"],
            ),
        }
        for jid in job_ids
    ])
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    upload_page._render_schema_review(_Service(engine), None)


def test_confirming_completes_every_job_that_paused(tmp_path):
    from streamlit.testing.v1 import AppTest

    db_path = tmp_path / "ledger.db"
    svc = ImportService(_engine(db_path))
    waiting = "⏸️ waiting"
    paused = []
    for _ in range(2):
        job = svc.create_job(n_files=1)
        svc.update_job(job.id, status="completed", status_message=waiting, n_transactions=0)
        paused.append(job.id)
    # A later job imported on its own: it must not be touched.
    auto = svc.create_job(n_files=1)
    svc.update_job(auto.id, status="completed", status_message="auto", n_transactions=66)

    app = AppTest.from_function(
        _review_script, kwargs={"db_path": str(db_path), "job_ids": paused},
    )
    app.run(timeout=30)
    assert not app.exception, app.exception
    [b for b in app.button if b.proto.type == "primary"][0].click().run(timeout=30)
    assert not app.exception, app.exception

    from db.models import ImportJob
    from sqlalchemy.orm import Session

    with Session(_engine(db_path)) as s:
        jobs = {j.id: j for j in s.query(ImportJob).all()}
    for jid in paused:
        assert jobs[jid].n_transactions == 35
        assert jobs[jid].status_message.startswith("✅")
    assert jobs[auto.id].status_message == "auto"
    assert jobs[auto.id].n_transactions == 66
