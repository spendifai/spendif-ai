"""A running import that stops moving says so, and can be ended.

WHY IT EXISTS
    A model that hung while loading or answering left the job "running" with
    the bar still. The founder's case sat two hours on 3.83%, and the only way
    to tell slow from dead was a query on the database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from db.models import ImportJob, create_tables, get_session
from services.import_service import ImportService


@pytest.fixture()
def svc(tmp_path):
    engine = create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))
    return ImportService(engine)


def _job(svc) -> int:
    with get_session(svc.engine) as s:
        job = ImportJob(status="running", progress=0.04)
        s.add(job)
        s.commit()
        return job.id


def test_every_update_is_a_sign_of_life(svc):
    jid = _job(svc)
    svc.update_job(jid, progress=0.05)
    job = svc.get_latest_job()
    assert job.last_ping is not None
    assert svc.seconds_without_progress(job) < 5


def test_a_job_that_stopped_moving_is_measured_from_its_last_update(svc):
    jid = _job(svc)
    svc.update_job(jid, progress=0.05)
    job = svc.get_latest_job()
    later = job.last_ping.replace(tzinfo=timezone.utc) + timedelta(minutes=7)
    assert svc.seconds_without_progress(job, now=later) >= svc.STALL_SECONDS


def test_a_job_never_updated_counts_from_its_start(svc):
    _job(svc)
    job = svc.get_latest_job()
    assert job.last_ping is None
    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    assert svc.seconds_without_progress(job, now=later) >= svc.STALL_SECONDS


def test_only_a_running_job_can_be_stalled(svc):
    jid = _job(svc)
    svc.update_job(jid, status="completed")
    job = svc.get_latest_job()
    assert svc.seconds_without_progress(job, now=datetime.now(timezone.utc) + timedelta(hours=2)) == 0


def test_stopping_frees_the_import_form(svc):
    jid = _job(svc)
    svc.stop_job(jid, "stopped")
    job = svc.get_latest_job()
    assert job.status == "error"
    assert job.completed_at is not None
    assert job.status_message == "stopped"
