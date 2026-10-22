"""Every duration the Import page shows comes with the time of day: start and
expected end ("fine prevista alle 15:36"), not only "about 18 min"; while it
runs, the time left is for all the files, and says so."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ui.upload_page import _clock, _clock_in

_KEYS = {
    "upload.eta.history": {"what", "time", "end"},
    "upload.eta.first": {"what", "time", "end"},
    "upload.eta.remaining": {"time", "what", "end", "start"},
    "upload.progress_auto_elapsed": {"pct", "what", "elapsed", "start", "end"},
    "guided.checklist.import_n": {"what"},
    "upload.completed_in": {"elapsed", "start", "end"},
    "upload.eta.if_fast": {"end"},
}


def test_clock_is_hour_and_minute_of_the_day_here():
    dt = datetime(2026, 10, 20, 13, 36, tzinfo=timezone.utc)
    assert _clock(dt) == dt.astimezone().strftime("%H:%M")
    assert re.fullmatch(r"\d\d:\d\d", _clock_in(18 * 60))


@pytest.mark.parametrize("lang", ["it", "en", "de", "es", "fr"])
def test_every_duration_text_carries_the_times(lang):
    strings = json.loads((Path(__file__).parent.parent / "ui" / "i18n" / f"{lang}.json")
                         .read_text(encoding="utf-8"))
    for key, fields in _KEYS.items():
        assert set(re.findall(r"\{(\w+)\}", strings[key])) == fields, (lang, key)


def test_file_shares_go_by_rows_not_by_file_count():
    from core.import_eta import file_spans
    assert file_spans([226, 51, 205]) == pytest.approx(
        [(0.0, 226 / 482), (226 / 482, 277 / 482), (277 / 482, 1.0)])
    assert file_spans([0, 0]) == [(0.0, 0.5), (0.5, 1.0)]     # no counts: equal shares
    assert file_spans([]) == []


def test_time_left_is_for_the_whole_import_whatever_file_is_running():
    """Halfway through the rows at a steady pace, half the time is left,
    even if the file in progress is the big one."""
    from core.import_eta import file_spans, remaining_seconds
    spans = file_spans([400, 100, 100])                 # 600 rows, 1 s each
    start, end = spans[0]
    pct = start + (end - start) * 0.75                  # 300 rows done
    assert remaining_seconds(300, pct, 600) == pytest.approx(300)


@pytest.mark.parametrize("lang, n_files, n_rows, text", [
    ("it", 6, 223, "6 file per un totale di 223 righe"),
    ("it", 1, 1, "1 file per un totale di 1 riga"),
    ("en", 6, 223, "6 files, 223 rows in all"),
    ("de", 1, 223, "1 Datei mit insgesamt 223 Zeilen"),
])
def test_files_and_rows(lang, n_files, n_rows, text):
    from ui.i18n import get_language, set_language
    from ui.widgets.staged_import import files_and_rows
    before = get_language()
    set_language(lang)
    try:
        assert files_and_rows(n_files, n_rows) == text
    finally:
        set_language(before)


def test_the_check_list_counts_the_rows_of_the_files_on_hold(tmp_path):
    from sqlalchemy import create_engine
    from db.models import create_tables
    from services.guided_flow_service import GuidedFlowService
    eng = create_tables(create_engine(f"sqlite:///{tmp_path / 'ledger.db'}"))
    csv = b"Data;Descrizione;Importo\n01/02/2026;A;-1,00\n02/02/2026;B;-2,00\n03/02/2026;C;-3,00\n"
    assert GuidedFlowService(eng).count_rows([("a.csv", csv), ("b.csv", csv)]) == 6
