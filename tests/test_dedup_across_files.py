"""The same movement exported in two formats is recognised across files.

Founder's case (2026-10-20): LOUIS VUITTON and ROSSIGNOLI entered twice, from
AMEX_LUIGI.xlsx and amex_movimenti-2024.xlsx. Founder's worry: the same
transfer made more than once on one day must not be lost.
"""
from __future__ import annotations

from core.dedup import find_twins, same_movement


def _m(tx_id, desc, amount=-2250, day="2024-10-04", account="amex"):
    return {"id": tx_id, "account_label": account, "date": day, "amount": amount,
            "raw_description": desc}


def test_the_founders_twins_are_recognised():
    saved = [_m("old1", "LVMH-DIV. LOUIS VUITTON MILANO"),
             _m("old2", "ROSSIGNOLI              MILANO", -2000, "2024-01-13")]
    incoming = [
        _m("new1", "LVMH-DIV. LOUIS VUITTON MILANO VIA UGO FOSCOLO\n5\nMI\nMILANO ITALY AT24279003"),
        _m("new2", "ROSSIGNOLI              MILANO CSO GIUSEPPE GARIBALDI", -2000, "2024-01-13"),
    ]
    assert {k: v["id"] for k, v in find_twins(incoming, saved).items()} == {
        "new1": "old1", "new2": "old2"}


def test_one_opens_the_other():
    assert same_movement("G U                     *", "G U * Foreign Spend Amount: 20 USD")


def test_no_word_in_common_is_no_twin():
    assert not same_movement("ESSELUNGA CORSICO", "BAR ROSSI")
    assert find_twins([_m("n", "ESSELUNGA")], [_m("o", "BAR ROSSI")]) == {}


def test_amount_date_account_and_direction_must_all_match():
    saved = [_m("o", "GIROCONTO MARIO", -500)]
    assert find_twins([_m("n", "GIROCONTO MARIO", 500)], saved) == {}           # direction
    assert find_twins([_m("n", "GIROCONTO MARIO", -501)], saved) == {}          # amount
    assert find_twins([_m("n", "GIROCONTO MARIO", -500, "2024-10-05")], saved) == {}  # date
    assert find_twins([_m("n", "GIROCONTO MARIO", -500, account="bbva")], saved) == {}  # account


# ── Transfers made more than once on the same day ────────────────────────────

def test_two_identical_transfers_in_both_files_stay_two():
    saved = [_m("a1", "GIROCONTO VERSO BBVA", -500), _m("a2", "GIROCONTO VERSO BBVA", -500)]
    incoming = [_m("b1", "GIROCONTO VERSO BBVA RIF 1", -500),
                _m("b2", "GIROCONTO VERSO BBVA RIF 2", -500)]
    twins = find_twins(incoming, saved)
    assert set(twins) == {"b1", "b2"}                              # both repeat
    assert {v["id"] for v in twins.values()} == {"a1", "a2"}       # each paired once


def test_a_transfer_only_in_the_new_file_is_kept():
    saved = [_m("a1", "GIROCONTO VERSO BBVA", -500)]
    incoming = [_m("b1", "GIROCONTO VERSO BBVA", -500), _m("b2", "GIROCONTO VERSO BBVA", -500)]
    assert set(find_twins(incoming, saved)) == {"b1"}             # b2 is new: 2 in all


def test_the_two_halves_of_a_transfer_are_not_twins():
    saved = [_m("out", "GIROCONTO VERSO BBVA", -500, account="intesa")]
    incoming = [_m("in", "GIROCONTO DA INTESA", 500, account="bbva")]
    assert find_twins(incoming, saved) == {}


# ── In the import ────────────────────────────────────────────────────────────

def test_the_import_skips_a_twin_before_the_model_and_reports_it(monkeypatch):
    import csv
    import io
    from core import orchestrator
    from core.categorizer import CategorizationResult
    from core.models import CategorySource, Confidence, DocumentType, SignConvention
    from core.orchestrator import ProcessingConfig, process_file
    from core.schemas import DocumentSchema
    from tests.test_giroconto_rule import _TAXONOMY

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    w.writerow(["03/01/2026", "LVMH-DIV. LOUIS VUITTON MILANO VIA UGO FOSCOLO", "-2250,00"])
    w.writerow(["04/01/2026", "ESSELUNGA CORSICO", "-40,00"])
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="amex", confidence=Confidence.high,
        confidence_score=1.0,
    )
    categorized: list[str] = []

    def _categorize(transactions, **kwargs):
        categorized.extend(t["description"] for t in transactions)
        return [CategorizationResult(category="Spesa", subcategory="Supermercato",
                                     confidence=Confidence.high, source=CategorySource.llm)
                for _ in transactions]

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", lambda txs, **kwargs: txs)
    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)

    asked = []

    def _other_files(account, dates, source_file):
        asked.append((account, dates, source_file))
        return [{"id": "old", "account_label": "amex", "date": "2026-01-03", "amount": -2250,
                 "raw_description": "LVMH-DIV. LOUIS VUITTON MILANO", "filename": "AMEX_LUIGI.xlsx"}]

    result = process_file(buf.getvalue().encode("utf-8"), "amex_2026.csv", ProcessingConfig(),
                          _TAXONOMY, [], other_files_loader=_other_files)
    assert asked == [("amex", ["2026-01-03", "2026-01-04"], "amex_2026.csv")]
    assert [t["raw_description"] for t in result.transactions] == ["ESSELUNGA CORSICO"]
    assert result.skipped_count == 1
    assert [(tw["date"], tw["twin_file"]) for tw in result.twins_skipped] == [
        ("2026-01-03", "AMEX_LUIGI.xlsx")]
    assert all("LOUIS" not in d.upper() for d in categorized)      # never sent to the model


def test_the_ledger_offers_only_movements_of_other_files(tmp_path):
    from decimal import Decimal
    from sqlalchemy import create_engine
    from db.models import ImportBatch, Transaction, create_tables, get_session
    from db.repository import get_movements_from_other_files

    eng = create_tables(create_engine(f"sqlite:///{tmp_path / 'l.db'}"))
    with get_session(eng) as s:
        for i, fn in enumerate(["A.xlsx", "B.xlsx"]):
            b = ImportBatch(sha256=str(i) * 64, filename=fn, n_transactions=1)
            s.add(b)
            s.flush()
            s.add(Transaction(id=f"t{i}", batch_id=b.id, date="2024-10-04", amount=Decimal("-2250"),
                              description="LV", raw_description="LOUIS VUITTON",
                              source_file=fn, account_label="Amex", tx_type="card_tx"))
        s.commit()
        got = get_movements_from_other_files(s, "amex", ["2024-10-04"], "B.xlsx")
    assert [(m["id"], m["filename"]) for m in got] == [("t0", "A.xlsx")]
