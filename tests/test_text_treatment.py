"""Text treatment: one rule for every comparison of descriptions.

Spec: documents/04_software_engineering/21_trattamento_testo.md.
"""
from __future__ import annotations

import pytest

from core.normalizer import tidy_text
from core.rule_key import same_text


@pytest.mark.parametrize("before, after", [
    ("GOOGLE*CLOUD", "GOOGLE CLOUD"),
    ("SEVEN ELEVEN *", "SEVEN ELEVEN"),
    ("AEON MALL KYOTO -KYOTO- *", "AEON MALL KYOTO KYOTO"),
    ("CLIVATI 1969 S.R.L.", "CLIVATI 1969 SRL"),
    ("CLIVATI 1969 S R L", "CLIVATI 1969 SRL"),
    ("A.T.M. 47", "ATM 47"),
    ("PAGAMENTO A (ITA)", "PAGAMENTO A ITA"),
    ("AMAZON.IT", "AMAZON IT"),
    ("ADDEBITO IN C/C", "ADDEBITO IN CC"),
    ("  under_score   doppi   spazi  ", "under score doppi spazi"),
    ("Caffè Città", "Caffè Città"),                 # accented letters stay
    ("イオンモール 京都", "イオンモール 京都"),         # other alphabets stay
    ("***", ""),
])
def test_tidy_text(before, after):
    assert tidy_text(before) == after


def test_exact_match_ignores_case_accents_and_punctuation_only():
    assert same_text("GOOGLE*CLOUD", "google cloud")
    assert same_text("Caffè Rossi", "CAFFE ROSSI")
    assert same_text("イオンモール", "イオンモール")
    assert not same_text("イオンモール", "ハネダ")      # not "both empty"
    assert not same_text("***", "")                       # never on empty text
    assert not same_text("TAMOIL", "TAMOIL GAGGIANO")


def test_merchant_list_reads_tidied_text_and_keeps_its_patterns():
    from core.nsi_lookup import NsiLookup
    nsi = NsiLookup()
    assert nsi.lookup("7-ELEVEN TOKYO").brand == "7-Eleven"
    assert nsi.lookup("7 ELEVEN TOKYO").brand == "7-Eleven"
    assert nsi.lookup("LEROY ME").brand == "Leroy Merlin"               # truncated prefix
    # a pattern ending with a separator wants another word after it
    assert (nsi.lookup("KEISEI SKYLINER CHIBA") or type("x", (), {"brand": None})).brand != "Skyline Chili"
    assert (nsi.lookup("AIRCHINA LIMITED BEIJING") or type("x", (), {"brand": None})).brand != "Beijing Bites"


def test_the_pipeline_saves_a_tidy_counterpart_and_an_intact_raw(monkeypatch):
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
    w.writerow(["03/01/2026", "GOOGLE*CLOUD 3CL96J", "-4,00"])
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="amex", confidence=Confidence.high,
        confidence_score=1.0,
    )

    def _clean(txs, **kw):
        for t in txs:
            t["description"] = "GOOGLE*CLOUD"          # what the model returned
        return txs

    def _categorize(*args, **kw):
        txs = kw.get("transactions", args[0] if args else [])
        return [CategorizationResult(category="Altro", subcategory="", confidence=Confidence.high,
                                     source=CategorySource.llm) for _ in txs]

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", _clean)
    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)
    result = process_file(buf.getvalue().encode("utf-8"), "a.csv", ProcessingConfig(), _TAXONOMY, [])
    [tx] = result.transactions
    assert tx["description"] == "GOOGLE CLOUD"
    assert tx["raw_description"] == "GOOGLE*CLOUD 3CL96J"


def test_initials_meet_in_every_reduction():
    """S.R.L., S R L and SRL are the same word for rules, equality and the merchant list."""
    from core.nsi_lookup import _tidy
    from core.rule_key import exact_key, same_text, words
    assert same_text("CLIVATI 1969 S.R.L.", "Clivati 1969 SRL")
    assert words("Clivati S R L") == words("CLIVATI SRL") == ["CLIVATI", "SRL"]
    assert exact_key("Y.K.K. Italia") == "ykk italia"
    assert _tidy("A.T.M. Milano") == "atm milano"


def test_the_apostrophe_is_part_of_the_word():
    """Founder, 2026-10-21: semantic, not syntactic. Shown as written, gone
    without splitting the word when comparing."""
    from core.normalizer import tidy_text
    from core.nsi_lookup import _tidy
    from core.rule_key import exact_key, same_text, words
    assert tidy_text("McDONALD'S*MILANO") == "McDONALD'S MILANO"
    assert tidy_text("L’OREAL") == "L'OREAL"                    # typographic one too
    assert tidy_text("'QUOTED'") == "QUOTED"                         # not inside a word
    assert same_text("McDonald's", "MCDONALDS") and same_text("McDonald’s", "mcdonalds")
    assert words("McDonald's Milano") == ["MCDONALDS", "MILANO"]
    assert exact_key("dell'Arte") == _tidy("DELL'ARTE") == "dellarte"
