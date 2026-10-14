"""Import time estimate: rows times the seconds per row seen on this computer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from core.import_eta import (
    DEFAULT_SECONDS_PER_ROW,
    duration_parts,
    estimate_total,
    remaining_seconds,
    seconds_per_row,
)
from db.models import ImportJob, create_tables, get_session
from services.import_service import ImportService


def test_seconds_per_row_pools_the_history():
    assert seconds_per_row([(100, 400.0), (50, 200.0)]) == 4.0
    assert seconds_per_row([]) is None


def test_estimate_uses_history_when_there_is_one():
    assert estimate_total(10, [(100, 300.0)]) == (30.0, True)


def test_first_import_uses_the_default_and_says_so():
    assert estimate_total(10, []) == (10 * DEFAULT_SECONDS_PER_ROW, False)


def test_remaining_starts_from_the_prior():
    assert remaining_seconds(elapsed=0, progress=0, prior_total=600) == 600
    assert remaining_seconds(elapsed=100, progress=0, prior_total=600) == 500


def test_remaining_follows_the_observed_pace_once_trusted():
    # 25% done in 300 s: the prior (600 s) was too optimistic, 900 s are left
    assert remaining_seconds(elapsed=300, progress=0.25, prior_total=600) == pytest.approx(900)


def test_remaining_blends_early_on():
    early = remaining_seconds(elapsed=60, progress=0.05, prior_total=600)
    observed_only = 60 / 0.05 * 0.95
    prior_only = 540
    assert min(prior_only, observed_only) < early < max(prior_only, observed_only)


def test_remaining_is_zero_when_done():
    assert remaining_seconds(elapsed=500, progress=1.0, prior_total=600) == 0


@pytest.mark.parametrize("secs, parts", [
    (20, (0, 0)), (60, (0, 1)), (61, (0, 2)), (3600, (1, 0)), (3725, (1, 3)),
])
def test_duration_rounds_up_to_the_minute(secs, parts):
    assert duration_parts(secs) == parts


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def _job(engine, n_rows, n_tx, secs, status="completed"):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with get_session(engine) as s:
        s.add(ImportJob(status=status, n_files=1, n_rows=n_rows, n_transactions=n_tx,
                        started_at=start, completed_at=start + timedelta(seconds=secs)))
        s.commit()


def test_history_counts_rows_read_and_falls_back_to_movements(engine):
    _job(engine, n_rows=200, n_tx=10, secs=400)       # rows read recorded
    _job(engine, n_rows=None, n_tx=100, secs=400)     # older job: movements only
    _job(engine, n_rows=None, n_tx=0, secs=30)        # nothing to divide by: left out
    _job(engine, n_rows=50, n_tx=50, secs=99, status="error")
    secs, from_history = ImportService(engine).estimate_import_seconds(30)
    assert from_history is True
    assert secs == pytest.approx(30 * 800 / 300)


def test_no_history_is_a_first_estimate(engine):
    secs, from_history = ImportService(engine).estimate_import_seconds(30)
    assert (secs, from_history) == (30 * DEFAULT_SECONDS_PER_ROW, False)


def test_rows_are_counted_for_known_formats_too(engine):
    data = b"date;amount;description\n2026-09-01;-1;a\n2026-09-02;-2;b\n2026-09-03;-3;c\n"
    assert ImportService(engine).count_rows([("f.csv", data)]) == 3
