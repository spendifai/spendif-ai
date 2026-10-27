"""The ledger side of the expense confirmation (spec 32, 4.2 and 5): the
search among the outflows, what is written on the movement, the "always like
this?" question and the model's one short call. Synthetic data only.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from db.models import CategoryRule, Transaction, create_tables, get_session
from services.expense_confirm_service import ExpenseConfirmService

TODAY = date(2026, 10, 25)


@pytest.fixture(autouse=True)
def _no_model(request, monkeypatch):
    """The words decide; the model is not there, unless a test gives one."""
    if "model_pick" in request.node.name:
        return
    monkeypatch.setattr(ExpenseConfirmService, "model_pick", lambda self, text, labels: None)


@pytest.fixture
def engine(tmp_path):
    return create_tables(create_engine(f"sqlite:///{tmp_path / 'e.db'}"))


def _add(engine, tx_id, amount, day, desc="ESSELUNGA", tx_type="expense", checked=False,
         category="Alimentari", subcategory="Spesa supermercato", source="llm"):
    with get_session(engine) as s:
        s.add(Transaction(
            id=tx_id, date=day, amount=Decimal(amount), description=desc, source_file="f",
            account_label="Conto", tx_type=tx_type, category=category, subcategory=subcategory,
            category_source=source, human_validated=checked))
        s.commit()


def _ids(cands):
    return sorted(c.tx_id for c in cands)


def test_outflows_by_amount_and_dates(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    _add(engine, "in", "23.40", "2026-10-12", tx_type="income")         # an inflow
    _add(engine, "far", "-23.40", "2026-10-20")                         # out of the window
    _add(engine, "other", "-23.41", "2026-10-12")                       # another amount
    _add(engine, "giro", "-23.40", "2026-10-12", tx_type="internal_out")  # a giroconto
    _add(engine, "done", "-23.40", "2026-10-12", checked=True)
    svc = ExpenseConfirmService(engine, today=TODAY)
    found = svc.outflows(Decimal("23.40"), Decimal("23.40"), date(2026, 10, 9), date(2026, 10, 15),
                         checked=False)
    assert _ids(found) == ["a"]
    c = found[0]
    assert (c.day, c.amount, c.counterpart.casefold(), c.category, c.subcategory) == (
        date(2026, 10, 12), Decimal("23.40"), "esselunga", "Alimentari", "Spesa supermercato")
    checked = svc.outflows(Decimal("23.40"), Decimal("23.40"), date(2026, 10, 9),
                           date(2026, 10, 15), checked=True)
    assert _ids(checked) == ["done"]


def test_outflows_in_a_range_of_cents(engine):
    _add(engine, "a", "-23.00", "2026-10-12")
    _add(engine, "b", "-23.99", "2026-10-12")
    _add(engine, "c", "-24.00", "2026-10-12")
    found = ExpenseConfirmService(engine).outflows(
        Decimal("23"), Decimal("23.99"), date(2026, 10, 9), date(2026, 10, 15), checked=False)
    assert _ids(found) == ["a", "b"]


def test_the_category_pairs_are_the_expense_side(engine):
    pairs = ExpenseConfirmService(engine).category_pairs()
    assert ("Alimentari", "Spesa supermercato") in pairs
    assert not any(c == "Lavoro dipendente" for c, _ in pairs)


def test_confirm_marks_checked_and_keeps_the_category(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    assert ExpenseConfirmService(engine).confirm("a", None) == []
    with get_session(engine) as s:
        tx = s.get(Transaction, "a")
        assert tx.human_validated and tx.validated_at is not None
        assert (tx.category, tx.category_source) == ("Alimentari", "llm")


def test_confirm_with_a_category_is_by_hand_and_checked(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    ExpenseConfirmService(engine).confirm("a", ("Casa", "Arredamento"))
    with get_session(engine) as s:
        tx = s.get(Transaction, "a")
        assert (tx.category, tx.subcategory, tx.category_source) == ("Casa", "Arredamento", "manual")
        assert tx.human_validated


def test_the_third_choice_by_hand_asks_always(engine):
    for i in range(2):
        _add(engine, f"m{i}", "-5", f"2026-09-0{i + 1}", category="Casa", subcategory="Arredamento",
             source="manual")
    _add(engine, "a", "-23.40", "2026-10-12")
    [q] = ExpenseConfirmService(engine).confirm("a", ("Casa", "Arredamento"))
    assert (q["category"], q["subcategory"], q["direction"], q["n"]) == ("Casa", "Arredamento", "out", 3)


def test_always_yes_uses_the_rule_writer(engine):
    written = []
    svc = ExpenseConfirmService(engine, rule_writer=lambda q: written.append(q) or "the note")
    q = {"description": "ESSELUNGA", "category": "Casa", "subcategory": "Arredamento",
         "direction": "out", "n": 3}
    assert svc.always_yes(q) == "the note"
    assert written == [q]


def test_always_no_is_not_asked_again(engine):
    for i in range(3):
        _add(engine, f"m{i}", "-5", f"2026-09-0{i + 1}", category="Casa", subcategory="Arredamento",
             source="manual")
    svc = ExpenseConfirmService(engine)
    [q] = svc.confirm("m2", ("Casa", "Arredamento"))
    svc.always_no(q)
    assert svc.confirm("m1", ("Casa", "Arredamento")) == []


def test_a_whole_turn_through_the_service(engine):
    _add(engine, "a", "-23.40", "2026-10-24")
    svc = ExpenseConfirmService(engine, today=TODAY)
    msgs, state = svc.start()
    msgs, state = svc.reply(state, "ieri, 23 e 40")
    assert [m.key for m in msgs] == ["expense.found_one"]
    msgs, state = svc.reply(state, "sì")
    with get_session(engine) as s:
        assert s.get(Transaction, "a").human_validated


# ── The model's short call ───────────────────────────────────────────────────

def _fake_model(monkeypatch, answer=None, fail=False):
    calls = []

    def _call(primary, system_prompt, user_prompt, json_schema, **kw):
        calls.append(user_prompt)
        if fail:
            raise RuntimeError("model down")
        return answer, "fake"

    monkeypatch.setattr("core.llm_backends.call_with_fallback", _call)
    return calls


def test_model_pick_returns_the_index(engine, monkeypatch):
    calls = _fake_model(monkeypatch, {"index": 1})
    svc = ExpenseConfirmService(engine, backend=object())
    assert svc.model_pick("rubinetto", ["Casa / Arredamento", "Casa / Manutenzione"]) == 1
    assert "rubinetto" in calls[0] and "Casa / Manutenzione" in calls[0]


@pytest.mark.parametrize("answer", [{"index": -1}, {"index": 5}, {}, None, {"index": "x"}])
def test_model_pick_without_a_valid_answer(engine, monkeypatch, answer):
    _fake_model(monkeypatch, answer)
    assert ExpenseConfirmService(engine, backend=object()).model_pick("x", ["A / B"]) is None


def test_model_pick_when_the_model_fails(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    assert ExpenseConfirmService(engine, backend=object()).model_pick("x", ["A / B"]) is None


def test_no_model_call_while_an_import_runs(engine, monkeypatch):
    from db.repository import create_import_job
    calls = _fake_model(monkeypatch, {"index": 0})
    with get_session(engine) as s:
        create_import_job(s, n_files=1)
        s.commit()
    assert ExpenseConfirmService(engine, backend=object()).model_pick("x", ["A / B"]) is None
    assert calls == []


def test_the_prompt_is_signed():
    from core.prompt_guard import verify_prompt_integrity
    assert verify_prompt_integrity() == []


def test_no_rule_is_created_by_a_confirmation(engine):
    _add(engine, "a", "-23.40", "2026-10-12")
    ExpenseConfirmService(engine).confirm("a", ("Casa", "Arredamento"))
    with get_session(engine) as s:
        assert s.query(CategoryRule).count() == 0


def test_last_date_is_the_latest_movement(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    assert svc.last_date() is None
    _add(engine, "a", "-5", "2026-10-09")
    _add(engine, "b", "100", "2026-10-18", tx_type="income")
    assert svc.last_date() == date(2026, 10, 18)


# ── The model reads an answer the words did not understand (spec 32, 4.8) ────

class _Sized:
    """A local model of a given file size."""
    def __init__(self, size):
        self.model_size_bytes = size


def test_model_intent_maps_the_label_and_offers_only_the_intents_of_the_question(engine, monkeypatch):
    schemas = []

    def _call(primary, system_prompt, user_prompt, json_schema, **kw):
        schemas.append(json_schema["properties"]["intento"]["enum"])
        return {"intento": "chiudi"}, "fake"

    monkeypatch.setattr("core.llm_backends.call_with_fallback", _call)
    svc = ExpenseConfirmService(engine, backend=_Sized(5_000_000_000))
    assert svc.model_intent("waiting", "lasciamo perdere per oggi") == "close"
    assert schemas == [["chiudi", "correggi", "altro"]]


@pytest.mark.parametrize("answer", [{"intento": "conferma"}, {"intento": "boh"}, {}, None])
def test_model_intent_out_of_the_question_is_none(engine, monkeypatch, answer):
    _fake_model(monkeypatch, answer)
    assert ExpenseConfirmService(engine, backend=_Sized(5_000_000_000)).model_intent("waiting", "x") is None


def test_a_small_model_is_not_asked(engine, monkeypatch):
    calls = _fake_model(monkeypatch, {"intento": "conferma"})
    assert ExpenseConfirmService(engine, backend=_Sized(986_048_768)).model_intent("found", "boh") is None
    assert calls == []


def test_a_hosted_model_counts_as_large(engine, monkeypatch):
    _fake_model(monkeypatch, {"intento": "conferma"})
    assert ExpenseConfirmService(engine, backend=object()).model_intent("found", "mi sa di si") == "yes"


def test_model_intent_when_the_model_fails(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    assert ExpenseConfirmService(engine, backend=object()).model_intent("found", "x") is None


def test_the_answer_never_reaches_the_log(engine, monkeypatch, caplog):
    import logging
    _fake_model(monkeypatch, {"intento": "rifiuta"})
    with caplog.at_level(logging.INFO):
        ExpenseConfirmService(engine, backend=object()).model_intent("found", "macche proprio no")
    assert "asking the model" in caplog.text           # the log is there, the words are not
    assert "macche" not in caplog.text


# ── 4.4bis Receipts waiting for their movement ──────────────────────────────


def _tx(engine, tx_id):
    with get_session(engine) as s:
        tx = s.get(Transaction, tx_id)
        return tx.category, tx.subcategory, bool(tx.human_validated)


def _save(svc, day="2026-10-24", amount="23.40", what="mobile", pair=("Casa", "Arredamento")):
    svc.save_receipt(date.fromisoformat(day), Decimal(amount), what, pair)


def test_a_receipt_waits_and_counts_nowhere(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    assert svc.pending_receipts() == [{"id": 1, "day": date(2026, 10, 24), "amount": Decimal("23.40"),
                                       "what": "mobile", "cash": False, "category": "Casa",
                                       "subcategory": "Arredamento", "kind": "expense", "cash_tx_id": None}]
    with get_session(engine) as s:
        assert s.query(Transaction).count() == 0


def test_nothing_is_checked_without_the_users_yes(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    _add(engine, "a", "-23.40", "2026-10-26")                     # two days later: in the window
    matches = svc.receipt_matches()
    assert [(m.receipt_id, [c.tx_id for c in m.candidates]) for m in matches] == [(1, ["a"])]
    assert matches[0].what == "mobile" and matches[0].pair == ("Casa", "Arredamento")
    assert _tx(engine, "a")[2] is False and len(svc.pending_receipts()) == 1


@pytest.mark.parametrize("movements", [
    [],                                                               # not imported yet
    [("a", "-23.40", "2026-10-10")],                                  # out of the window
    [("a", "-23.41", "2026-10-24")],                                  # another amount
])
def test_no_movement_no_match(engine, movements):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    for tx_id, amount, day in movements:
        _add(engine, tx_id, amount, day)
    assert svc.receipt_matches() == []


def test_equal_movements_are_all_offered_the_closest_first(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    _add(engine, "far", "-23.40", "2026-10-26")
    _add(engine, "near", "-23.40", "2026-10-24")
    assert [c.tx_id for c in svc.receipt_matches()[0].candidates] == ["near", "far"]


def test_the_users_yes_checks_with_the_receipts_category(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    _add(engine, "a", "-23.40", "2026-10-24")
    svc.confirm_receipt(1, "a", ("Casa", "Arredamento"))
    assert _tx(engine, "a") == ("Casa", "Arredamento", True)
    assert svc.pending_receipts() == [] and svc.receipt_matches() == []


def test_the_conversation_starts_with_the_receipts_found(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    _add(engine, "a", "-23.40", "2026-10-24")
    msgs, _ = svc.start()
    assert [m.key for m in msgs] == ["expense.receipts_found_one"]


def test_a_receipt_can_be_forgotten(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    _save(svc)
    svc.delete_receipt(svc.pending_receipts()[0]["id"])
    assert svc.pending_receipts() == []


# ── 4.9 The expenses dictated to Siri ───────────────────────────────────────


def test_the_dictated_lines_become_receipts_and_the_file_empties(engine, tmp_path):
    svc = ExpenseConfirmService(engine, today=TODAY)
    inbox = tmp_path / "Spendif.ai" / "spese-da-siri.txt"
    inbox.parent.mkdir()
    inbox.write_text("2026-10-25 18:30 | ieri 23 e 40, caffè al bar\n"
                     "2026-10-25 19:00 | spesa supermercato 12 euro oggi\n"
                     "2026-10-25 19:10 | caffè al bar\n", encoding="utf-8")
    assert svc.collect_siri([inbox])[:2] == (2, 1)
    # emptied in place: the permission given to Shortcuts holds for this very file
    assert inbox.exists() and inbox.read_text(encoding="utf-8") == ""
    got = {(r["day"], r["amount"], r["what"], r["category"]) for r in svc.pending_receipts()}
    assert got == {(date(2026, 10, 24), Decimal("23.40"), "caffe al bar", ""),
                   (date(2026, 10, 25), Decimal("12.00"), "spesa supermercato", "Alimentari")}
    assert svc.unclear_sentences([inbox]) == ["caffè al bar"]
    svc.forget_unclear([inbox], "caffè al bar")
    assert svc.unclear_sentences([inbox]) == []


def test_no_file_nothing_to_do_but_the_folder_is_ready(engine, tmp_path):
    inbox = tmp_path / "Spendif.ai" / "spese-da-siri.txt"
    assert ExpenseConfirmService(engine, today=TODAY).collect_siri([inbox])[:2] == (0, 0)
    assert inbox.parent.is_dir()


def test_a_receipt_without_category_keeps_the_movements_at_the_check(engine, tmp_path):
    svc = ExpenseConfirmService(engine, today=TODAY)
    inbox = tmp_path / "spese-da-siri.txt"
    inbox.write_text("2026-10-25 18:30 | ieri 23 e 40, caffè al bar\n", encoding="utf-8")
    svc.collect_siri([inbox])
    _add(engine, "a", "-23.40", "2026-10-24")
    match = svc.receipt_matches()[0]
    assert match.pair is None and match.what == "caffe al bar"
    svc.confirm_receipt(match.receipt_id, "a", None)
    assert _tx(engine, "a") == ("Alimentari", "Spesa supermercato", True)


@pytest.mark.siri_files
def test_the_shortcuts_folder_only_once_the_command_is_installed(monkeypatch, tmp_path):
    monkeypatch.setattr("services.expense_confirm_service.sys.platform", "darwin")
    monkeypatch.setattr("services.expense_confirm_service.Path.home", lambda: tmp_path)   # never the user's folders
    assert len(ExpenseConfirmService.siri_files(False)) == 1
    files = ExpenseConfirmService.siri_files(True, "Le mie spese")
    assert len(files) == 2
    assert "iCloud~is~workflow~my~workflows" in str(files[1]) and files[1].parent.name == "Le mie spese"


@pytest.mark.siri_files
def test_no_siri_outside_apple_systems(monkeypatch, tmp_path):
    monkeypatch.setattr("services.expense_confirm_service.sys.platform", "linux")
    monkeypatch.setattr("services.expense_confirm_service.Path.home", lambda: tmp_path)
    assert ExpenseConfirmService.siri_files(True) == []


@pytest.mark.siri_files
def test_the_iphone_entries_reach_every_desktop_system(monkeypatch, tmp_path):
    """The desktop app runs on macOS, Windows and Linux (spec 34, 4): the
    iPhone app's entries are read on all three; on Windows from iCloud for
    Windows by default, on Linux from the folder set in Settings."""
    monkeypatch.setattr("services.expense_confirm_service.Path.home", lambda: tmp_path)
    monkeypatch.setattr("services.expense_confirm_service.sys.platform", "win32")
    windows = _entry(tmp_path / "iCloudDrive" / "iCloud~ai~spendif~ios", "a", "2026-10-25 18:30 | carta | 9 euro libro")
    assert windows in ExpenseConfirmService.siri_files(False)
    monkeypatch.setattr("services.expense_confirm_service.sys.platform", "linux")
    synced = _entry(tmp_path / "Sync" / "spese", "b", "2026-10-25 18:30 | carta | 9 euro libro")
    assert ExpenseConfirmService.siri_files(False, app_folder=str(synced.parent)) == [
        synced.parent / "spese-da-siri.txt", synced]


# ── The iPhone app's folder (spec 34, 4) ─────────────────────────────────────


def _entry(folder, name, line):
    """One file as the iPhone app writes it: one entry, one line.

    Args:
        folder: the app folder (a tmp_path in tests).
        name: the file name after "spesa-".
        line: the line, with date and kind fields.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"spesa-{name}.txt"
    path.write_text(line + "\n", encoding="utf-8")
    return path


@pytest.mark.siri_files
def test_the_app_folder_is_read_by_default_and_can_be_moved(monkeypatch, tmp_path):
    """The app's iCloud folder needs no setting; a shared folder replaces it."""
    monkeypatch.setattr("services.expense_confirm_service.sys.platform", "darwin")
    monkeypatch.setattr("services.expense_confirm_service.Path.home", lambda: tmp_path)
    app = tmp_path / "Library" / "Mobile Documents" / "iCloud~ai~spendif~ios" / "Documents"
    assert all("iCloud~ai~spendif~ios" not in str(f) for f in ExpenseConfirmService.siri_files(False))
    entry = _entry(app, "a", "2026-10-25 18:30 | carta | 12 euro pizza")
    assert entry in ExpenseConfirmService.siri_files(False)
    shared = tmp_path / "condivisa"
    other = _entry(shared, "b", "2026-10-25 18:30 | carta | 9 euro libro")
    files = ExpenseConfirmService.siri_files(False, app_folder=str(shared))
    assert other in files and entry not in files


def test_an_app_entry_is_saved_then_its_file_deleted(engine, tmp_path):
    """One file per entry: once saved, the file goes; nothing is read twice."""
    svc = ExpenseConfirmService(engine, today=TODAY)
    entry = _entry(tmp_path, "a", "2026-10-25 18:30 | carta | ieri 23 e 40, caffè al bar")
    assert svc.collect_siri([entry])[:2] == (1, 0)
    assert not entry.exists()
    assert [r["amount"] for r in svc.pending_receipts()] == [Decimal("23.40")]


def _link(engine):
    """The giroconto pairs, as (out, in, user's decision)."""
    from db.models import InternalTransferLink
    with get_session(engine) as s:
        return [(l.out_id, l.in_id, l.user_decision) for l in s.query(InternalTransferLink)]


def _row(engine, tx_id):
    """Type, category, amount and checked of one movement."""
    with get_session(engine) as s:
        tx = s.get(Transaction, tx_id)
        return tx.tx_type, tx.category, float(tx.amount), bool(tx.human_validated)


def test_a_cash_refund_is_an_inflow_on_the_cash_account(engine, tmp_path):
    """Spec 32, 4.10: in at once on the cash account; its category, named by
    no word, with no default chosen and no model here, is asked among the
    income ones."""
    from services.settings_service import SettingsService
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    SettingsService(engine).set("refund_category", "")       # "Nessuna": the chat asks
    entry = _entry(tmp_path, "a", "2026-10-25 18:30 | rimborso-contanti | 30 euro reso scarpe")
    report = svc.collect_siri([entry])
    assert report[:2] == (1, 0) and not entry.exists()
    rows = _cash_rows(engine)
    assert len(rows) == 1 and rows[0][1] == 30.0 and rows[0][3] is None and rows[0][5]
    assert [(i.kind, i.income, i.pair) for i in report.confirm] == [("cash", True, None)]
    assert svc.pending_receipts() == []


def test_the_words_name_the_income_category_of_a_refund(engine, tmp_path):
    """The user's own income taxonomy, read by the words: no question."""
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    report = svc.collect_siri([_entry(tmp_path, "a", "2026-10-25 18:30 | rimborso-contanti | 30 euro rimborso generico")])
    assert report.confirm == []
    assert _cash_rows(engine)[0][3] == "Trasferimenti e rimborsi"


def test_the_default_refund_category_of_the_settings(engine, tmp_path):
    """Chosen in Settings among the income categories, it is used when the
    words name none; an empty or stale choice asks as before."""
    from services.settings_service import SettingsService
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    assert ("Trasferimenti e rimborsi", "Rimborso generico") in svc.income_pairs()
    # never chosen: the shipped default of the language
    assert svc.default_refund_pair() == ("Trasferimenti e rimborsi", "Rimborso generico")
    SettingsService(engine).set("refund_category", "Trasferimenti e rimborsi|Storno / rettifica bancaria")
    assert svc.default_refund_pair() == ("Trasferimenti e rimborsi", "Storno / rettifica bancaria")
    report = svc.collect_siri([_entry(tmp_path, "a", "2026-10-25 18:30 | rimborso-contanti | 30 euro reso scarpe")])
    assert report.confirm == [] and _cash_rows(engine)[0][3] == "Trasferimenti e rimborsi"
    SettingsService(engine).set("refund_category", "Categoria|Sparita")
    report = svc.collect_siri([_entry(tmp_path, "b", "2026-10-25 18:31 | rimborso-contanti | 9 euro reso libro")])
    assert [i.income for i in report.confirm] == [True]


def test_a_card_refund_waits_and_matches_an_inflow(engine, tmp_path):
    """Spec 32, 4.10: waits for the statement, matched to an unchecked credit
    with its amount, never to an outflow."""
    svc = ExpenseConfirmService(engine, today=TODAY)
    entry = _entry(tmp_path, "a", "2026-10-25 18:30 | rimborso-carta | 30 euro rimborso generico")
    svc.collect_siri([entry])
    assert [r["kind"] for r in svc.pending_receipts()] == ["refund"]
    _add(engine, "out", "-30", "2026-10-25")
    _add(engine, "in", "30", "2026-10-26", desc="STORNO ZALANDO", tx_type="income", category=None, subcategory=None)
    match = svc.receipt_matches()[0]
    assert [c.tx_id for c in match.candidates] == ["in"]
    svc.confirm_receipt(match.receipt_id, "in", match.pair)
    assert _row(engine, "in") == ("income", "Trasferimenti e rimborsi", 30.0, True)
    assert svc.pending_receipts() == []


def test_a_withdrawal_is_a_transfer_confirmed_on_cash_waiting_on_the_bank(engine, tmp_path):
    """Spec 32, 4.10: the cash side at once, a checked transfer; the bank side
    waits, and the user's yes links the two as a transfer."""
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    entry = _entry(tmp_path, "a", "2026-10-25 18:30 | prelievo | 50 euro")
    svc.collect_siri([entry])
    cash = _cash_rows(engine)
    assert len(cash) == 1 and cash[0][1] == 50.0 and cash[0][3] is None and cash[0][5]
    waiting = svc.pending_receipts()
    assert [r["kind"] for r in waiting] == ["withdrawal"] and waiting[0]["cash_tx_id"]
    _add(engine, "atm", "-50", "2026-10-26", desc="PRELIEVO BANCOMAT", tx_type="internal_out",
         category=None, subcategory=None)
    match = svc.receipt_matches()[0]
    assert [c.tx_id for c in match.candidates] == ["atm"]
    svc.confirm_receipt(match.receipt_id, "atm", None)
    assert _row(engine, "atm") == ("internal_out", None, -50.0, True)
    assert _link(engine) == [("atm", waiting[0]["cash_tx_id"], "yes")]
    assert svc.pending_receipts() == []


def test_a_withdrawal_without_cash_account_waits_for_it(engine, tmp_path):
    """Spec 32, 4.10: no cash account, the cash side waits like a cash
    expense; once the account exists it is written, the bank side still waits."""
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.collect_siri([_entry(tmp_path, "a", "2026-10-25 18:30 | prelievo | 50 euro")])
    assert _cash_rows(engine) == [] and svc.pending_receipts()[0]["cash_tx_id"] is None
    svc.create_cash_account()
    svc.flush_cash_receipts()
    assert len(_cash_rows(engine)) == 1
    assert [r["kind"] for r in svc.pending_receipts()] == ["withdrawal"]
    assert svc.pending_receipts()[0]["cash_tx_id"]


def test_an_unclear_app_entry_is_kept_once_to_complete(engine, tmp_path):
    """A sentence without amount goes to the sentences to complete, its file
    goes, and the sentence is listed once even with several files around."""
    svc = ExpenseConfirmService(engine, today=TODAY)
    unclear = _entry(tmp_path, "a", "2026-10-25 18:30 | carta | caffè al bar")
    other = _entry(tmp_path, "b", "2026-10-25 18:31 | carta | 9 euro libro")
    assert svc.collect_siri([unclear, other])[:2] == (1, 1)
    assert not unclear.exists()
    assert svc.unclear_sentences([unclear, other]) == ["caffè al bar"]


# ── 4.4 The cash account ────────────────────────────────────────────────────


def _cash_rows(engine):
    with get_session(engine) as s:
        return [(t.date, float(t.amount), t.description, t.category, t.account_label, bool(t.human_validated),
                 t.category_source) for t in s.query(Transaction).filter(Transaction.doc_type == "cash")]


def test_no_cash_account_until_one_is_chosen_or_created(engine):
    from services.settings_service import SettingsService
    svc = ExpenseConfirmService(engine, today=TODAY)
    assert svc.cash_account() is None
    SettingsService(engine).set("ui_language", "it")
    assert svc.create_cash_account() == "Contanti" and svc.cash_account() == "Contanti"
    acc = [a for a in SettingsService(engine).get_accounts() if a.name == "Contanti"]
    assert acc and acc[0].account_type == "cash"


def test_a_cash_movement_is_written_checked(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.cash_movement(date(2026, 10, 24), Decimal("4"), "giornale", ("Svago", "Libri"), "Contanti")
    assert _cash_rows(engine) == [("2026-10-24", -4.0, "Giornale", "Svago", "Contanti", True, "manual")]


def test_cash_receipts_waiting_move_into_the_account(engine):
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.save_receipt(date(2026, 10, 24), Decimal("4"), "giornale", None, cash=True)
    svc.save_receipt(date(2026, 10, 24), Decimal("9"), "viti", None, cash=False)
    assert svc.flush_cash_receipts() == 0                    # no cash account yet: they wait
    svc.create_cash_account()
    assert svc.flush_cash_receipts() == 1
    assert [r["what"] for r in svc.pending_receipts()] == ["viti"]
    assert [row[2] for row in _cash_rows(engine)] == ["Giornale"]


def test_siri_cash_lines_go_straight_into_the_cash_account(engine, tmp_path):
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    inbox = tmp_path / "spese-da-siri.txt"
    inbox.write_text("2026-10-25 18:30 | contanti | ieri 4 euro, giornale\n", encoding="utf-8")
    assert svc.collect_siri([inbox])[:2] == (1, 0)
    assert svc.pending_receipts() == [] and len(_cash_rows(engine)) == 1


def test_the_report_has_one_note_per_line_for_the_conversation(engine, tmp_path):
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    inbox = tmp_path / "spese-da-siri.txt"
    inbox.write_text("2026-10-25 18:30 | contanti | ieri 4 euro, giornale\n"
                     "2026-10-25 18:31 | carta | ieri 9 euro, viti\n"
                     "2026-10-25 18:32 | carta | viti\n", encoding="utf-8")
    report = svc.collect_siri([inbox])
    assert [key for key, _ in report.notes] == ["expense.siri_note_cash", "expense.siri_note_saved",
                                               "expense.siri_note_unclear"]
    assert report.notes[0][1]["account"] == "Contanti"


def test_the_model_proposes_and_the_user_confirms_the_siri_category(engine, tmp_path, monkeypatch):
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    pairs = svc.category_pairs()
    target = next(p for p in pairs if p[0] == "Alimentari")
    monkeypatch.setattr(ExpenseConfirmService, "model_pick", lambda self, text, labels: pairs.index(target))
    inbox = tmp_path / "spese-da-siri.txt"
    inbox.write_text("2026-10-25 18:30 | contanti | ieri 4 euro, pane\n", encoding="utf-8")
    report = svc.collect_siri([inbox])
    assert len(report.confirm) == 1 and report.confirm[0].pair == target and report.confirm[0].kind == "cash"
    rows = _cash_rows(engine)
    assert rows[0][3] == target[0] and rows[0][6] == "llm"           # the model's, not the user's
    svc.siri_category(report.confirm[0], target)
    assert _cash_rows(engine)[0][6] == "manual"


def test_a_category_named_by_the_words_is_not_asked_again(engine, tmp_path):
    svc = ExpenseConfirmService(engine, today=TODAY)
    svc.create_cash_account()
    inbox = tmp_path / "spese-da-siri.txt"
    inbox.write_text("2026-10-25 18:30 | contanti | ieri 12 euro spesa supermercato\n", encoding="utf-8")
    assert svc.collect_siri([inbox]).confirm == []
