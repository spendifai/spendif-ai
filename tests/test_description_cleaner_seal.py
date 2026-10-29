"""The seal on the description cleaner: the model may only remove words (spec 35).

All LLM calls are mocked: each test scripts the answers the model gives, in
order, and checks what becomes the counterpart and what the seal counted.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.description_cleaner import SEAL_STAT_KEYS, _process_group, clean_descriptions_batch, sealed
from core.sanitizer import SanitizationConfig


def _scripted_model(answers: list[str | None], calls: list[dict] | None = None):
    """A stand-in for call_with_fallback that answers from a script.

    Exists so a test can say "the batch answers X, the first retry Y" without
    caring how many descriptions a request carries.

    Args:
        answers: the name the model returns at each call, in order; None makes
            that call fail (call_with_fallback returning None).
        calls: when given, every call's keyword arguments are appended to it,
            for the tests that check how a retry is asked.
    """
    script = iter(answers)

    def fake_call(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        answer = next(script)
        if answer is None:
            return None, "quarantine"
        return {"results": [{"idx": 0, "name": answer}]}, "mock"

    return fake_call


def _one_expense(description: str) -> list[dict]:
    """A single expense movement whose raw and current description are the same text.

    Args:
        description: the description as read from the bank file.
    """
    return [{"description": description, "raw_description": description, "amount": "-10.00"}]


# ─────────────────────────────────────────────────────────────────────────────
# The seal itself: the table of spec 35, 3
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw_description, answer, expected", [
    ("PAGAMENTO POS ESSELUNGA SPA MILANO", "Esselunga", True),
    ("BONIFICO DA ROSSI MARIO", "Mario Rossi", True),
    ("NETFLIX.COM AMSTERDAM", "Netflix", True),
    ("AMAZONMKTPLACE LUXEMBOURG", "Amazon", True),
    ("AMAZONMKTPLACE LUXEMBOURG", "Amazon Marketplace", False),
    ("BONIFICO DA ROSSI MARIO", "Mario Bianchi", False),
    ("FARM. S. ANNA", "Farmacia Sant'Anna", False),
    ("COOP LOMBARDIA", "Esselunga", False),
])
def test_seal_table_of_the_spec(raw_description, answer, expected):
    assert sealed(answer, raw_description) is expected


def test_seal_ignores_case_accents_and_punctuation():
    assert sealed("caffe' della stazione", "CAFFÈ DELLA STAZIONE SRL")
    assert sealed("Clivati 1969 S.r.l.", "CLIVATI 1969 S R L")


def test_short_word_must_be_whole():
    assert not sealed("Bar", "BARCLAYS BANK")
    assert sealed("Bar Sport", "BAR SPORT MILANO")


def test_empty_answer_is_not_sealed():
    assert not sealed("", "COOP LOMBARDIA")
    assert not sealed("***", "COOP LOMBARDIA")


def test_japanese_description_contiguous_piece_passes_invented_name_does_not():
    raw_description = "セブンイレブン 渋谷店"
    assert sealed("セブンイレブン", raw_description)
    assert not sealed("ローソン", raw_description)


# ─────────────────────────────────────────────────────────────────────────────
# Inside the cleaner: what becomes the counterpart
# ─────────────────────────────────────────────────────────────────────────────

def test_owner_name_restored_from_placeholder_passes_the_seal():
    config = SanitizationConfig(owner_names=["Mario Rossi"], description_language="it")
    txs = _one_expense("BONIFICO DA Mario Rossi")

    def echo_after_da(**kwargs):
        # Answers with what follows "DA " in the redacted text: the fake name
        # the cleaner put in place of the owner's.
        sent = json.loads(kwargs["user_prompt"].split("\n\n", 1)[1])[0]["name"]
        return {"results": [{"idx": 0, "name": sent.split("DA ", 1)[1]}]}, "mock"

    with patch("core.description_cleaner.call_with_fallback", side_effect=echo_after_da):
        _process_group(txs, [0], "sys", MagicMock(), None, 15, "test", "expense", sanitize_config=config)
    assert txs[0]["description"] == "Mario Rossi"


def test_refused_answer_then_good_retry_is_accepted():
    txs = _one_expense("BONIFICO DA ROSSI MARIO")
    stats: dict[str, int] = {}
    with patch("core.description_cleaner.call_with_fallback", side_effect=_scripted_model(["Mario Bianchi", "Mario Rossi"])):
        _process_group(txs, [0], "sys", MagicMock(), None, 15, "test", "expense", seal_stats=stats)
    assert txs[0]["description"] == "Mario Rossi"
    assert stats == {"sent": 1, "accepted_first": 0, "accepted_retry": 1, "rejected": 0, "retry_calls": 1}


def test_four_invented_answers_leave_the_raw_description_after_three_retries():
    txs = _one_expense("BONIFICO DA ROSSI MARIO")
    calls: list[dict] = []
    stats: dict[str, int] = {}
    invented = ["Mario Bianchi", "Luigi Verdi", "Anna Neri", "Carlo Blu"]
    with patch("core.description_cleaner.call_with_fallback", side_effect=_scripted_model(invented, calls)):
        _process_group(txs, [0], "sys", MagicMock(), None, 15, "test", "expense", seal_stats=stats)
    assert txs[0]["description"] == "BONIFICO DA ROSSI MARIO"
    assert len(calls) == 4
    assert stats["rejected"] == 1 and stats["retry_calls"] == 3


def test_empty_retry_stops_the_retries():
    txs = _one_expense("BONIFICO DA ROSSI MARIO")
    calls: list[dict] = []
    with patch("core.description_cleaner.call_with_fallback", side_effect=_scripted_model(["Mario Bianchi", ""], calls)):
        _process_group(txs, [0], "sys", MagicMock(), None, 15, "test", "expense")
    assert txs[0]["description"] == "BONIFICO DA ROSSI MARIO"
    assert len(calls) == 2


def test_failed_retry_call_stops_the_retries():
    txs = _one_expense("BONIFICO DA ROSSI MARIO")
    calls: list[dict] = []
    with patch("core.description_cleaner.call_with_fallback", side_effect=_scripted_model(["Mario Bianchi", None], calls)):
        _process_group(txs, [0], "sys", MagicMock(), None, 15, "test", "expense")
    assert txs[0]["description"] == "BONIFICO DA ROSSI MARIO"
    assert len(calls) == 2


def test_retries_ask_alone_with_the_rule_and_change_temperature():
    txs = [
        {"description": d, "raw_description": d, "amount": "-10.00"}
        for d in ("BONIFICO DA ROSSI MARIO", "COOP LOMBARDIA")
    ]
    calls: list[dict] = []

    def batch_then_script(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            # The batch: the first answer invented, the second fine.
            return {"results": [{"idx": 0, "name": "Mario Bianchi"}, {"idx": 1, "name": "Coop"}]}, "mock"
        return {"results": [{"idx": 0, "name": "Luigi Verdi"}]}, "mock"

    with patch("core.description_cleaner.call_with_fallback", side_effect=batch_then_script):
        _process_group(txs, [0, 1], "sys", MagicMock(), None, 15, "test", "expense")

    retries = calls[1:]
    assert len(retries) == 3
    assert [r["temperature"] for r in retries] == [0.0, 0.3, 0.6]
    for retry in retries:
        assert retry["batch_size"] == 1
        assert "Use only words that appear in the description" in retry["system_prompt"]
        assert "BONIFICO DA ROSSI MARIO" in retry["user_prompt"]
        assert "COOP LOMBARDIA" not in retry["user_prompt"]
    assert txs[1]["description"] == "Coop"


def test_to_review_is_not_touched_by_the_seal():
    txs = _one_expense("BONIFICO DA ROSSI MARIO")
    txs[0]["to_review"] = False
    with patch("core.description_cleaner.call_with_fallback", side_effect=_scripted_model(["X Y Z"] * 4)):
        _process_group(txs, [0], "sys", MagicMock(), None, 15, "test", "expense")
    assert txs[0]["to_review"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Counts for the report, never a description in the log
# ─────────────────────────────────────────────────────────────────────────────

def test_public_api_fills_the_counts_and_logs_no_description():
    txs = [
        {"description": "ESSELUNGA MILANO", "raw_description": "ESSELUNGA MILANO", "amount": "-10.00"},
        {"description": "BONIFICO DA ROSSI MARIO", "raw_description": "BONIFICO DA ROSSI MARIO", "amount": "100.00"},
    ]
    stats: dict[str, int] = {}
    answers = ["Esselunga", "Mario Bianchi", "Anna Neri", "Luigi Verdi", "Carlo Blu"]
    with patch("core.description_cleaner.call_with_fallback", side_effect=_scripted_model(answers)), \
            patch("core.description_cleaner.logger") as fake_logger:
        clean_descriptions_batch(txs, MagicMock(), seal_stats=stats)

    assert set(stats) == set(SEAL_STAT_KEYS)
    assert stats == {"sent": 2, "accepted_first": 1, "accepted_retry": 0, "rejected": 1, "retry_calls": 3}
    logged = " ".join(str(c) for c in fake_logger.method_calls)
    for text in ("ESSELUNGA", "ROSSI", "Bianchi", "Neri", "Verdi"):
        assert text not in logged
