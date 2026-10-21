"""Rules written by the product: the merchant name as the text of a rule.

Spec: documents/04_software_engineering/18_regole_per_chiave.md. The key
comes from the merchant list (NSI) in the user's country or from the model,
passes a deterministic seal, and becomes a "starts with" rule; anything else
gives the exact description, as before.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.categorizer import CategoryRule
from core.rule_key import (
    KEY_CHECK, KEY_KNOWN, STARTS_WITH, key_confidence, nsi_key, seal, starts_with, words,
)
from db.models import Transaction, create_tables, get_session
from db.repository import (
    apply_all_rules_to_all_transactions, get_category_rules, get_transactions_by_rule_pattern,
)
from services.rule_service import RuleService


# ── Words and "starts with" ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Caffè Clivati", ["CAFFE", "CLIVATI"]),
    ("CLIVATI 1969 S.R.L.", ["CLIVATI", "1969", "SRL"]),   # spec 21: initials joined
    ("A.T.M. 47", ["ATM", "47"]),
    ("PAGAMENTO A ITA", ["PAGAMENTO", "A", "ITA"]),         # a lone letter stays
    ("GOOGLE*CLOUD", ["GOOGLE", "CLOUD"]),
    ("EOLO.IT", ["EOLO", "IT"]),
    ("", []),
    (None, []),
])
def test_words(text, expected):
    assert words(text) == expected


@pytest.mark.parametrize("description, pattern, expected", [
    ("CLIVATI 1969 MILANO", "CLIVATI", True),
    ("TAMOIL GAGGIANO", "TAMOIL", True),
    ("GOOGLEPLAY", "GOOGLE", False),           # whole words, not pieces
    ("MY GOOGLE STORE", "GOOGLE", False),      # the rule must open the description
    ("Google*Cloud Storage", "GOOGLE CLOUD", True),
    ("IMPOSTA DI BOLLO", "IMPOSTA DI BOLLO CONTO", False),
    ("ANYTHING", "", False),
])
def test_starts_with(description, pattern, expected):
    assert starts_with(description, pattern) is expected


def test_the_rule_type_matches():
    rule = CategoryRule(id=1, pattern="TAMOIL", match_type=STARTS_WITH, category="Trasporti",
                        subcategory="", doc_type=None)
    assert rule.matches("TAMOIL GAGGIANO")
    assert not rule.matches("TAMOILX")


# ── The seal ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("description, proposed, rule", [
    ("TAMOIL GAGGIANO", "TAMOIL", "TAMOIL"),
    ("CLIVATI 1969 S.R.L.", "CLIVATI", "CLIVATI"),
    ("GOOGLE*CLOUD", "GOOGLE CLOUD", "GOOGLE CLOUD"),
    ("AL CANTINONE VIA AGN", "CANTINONE", "AL CANTINONE"),   # must take its own description
    ("LEROY MERLIN CORSICO", "Leroy Merlin", "LEROY MERLIN"),
    ("FATTURA 53", "", None),                                 # emptied: exact
    ("F24", "F24", None),                                     # no word of 4 letters
    ("TAMOIL GAGGIANO", "TAMOIL SPA", None),                  # a word added
    ("TAMOIL GAGGIANO", "TAMOYL", None),                      # a word rewritten
    ("LEROY MERLIN", "MERLIN LEROY", None),                   # order changed
    ("TAMOIL GAGGIANO", None, None),
])
def test_seal(description, proposed, rule):
    assert seal(description, proposed) == rule


# ── The merchant list ────────────────────────────────────────────────────────

@dataclass
class _Match:
    brand: str
    countries: tuple


class _Lookup:
    def __init__(self, table):
        self.table = table

    def lookup(self, text, user_country=None):
        for word, match in self.table.items():
            if word in text.upper():
                return match
        return None


_LIST = _Lookup({
    "TAMOIL": _Match("Tamoil", ("CH", "IT")),
    "LIDL": _Match("Lidl", ()),                  # every country
    "PIZZA": _Match("&pizza", ("US",)),          # a common word elsewhere
    "MCDONALD": _Match("McDonald's", ()),        # spelled with an apostrophe
})


@pytest.mark.parametrize("description, country, key", [
    ("TAMOIL GAGGIANO", "IT", "TAMOIL"),
    ("TAMOIL GAGGIANO", "US", None),             # brand of other countries
    ("LIDL TREZZANO SUL NA", "IT", "LIDL"),
    ("PIZZA DA MARIO", "IT", None),
    # The apostrophe is part of the word (2026-10-21): McDonald's is MCDONALDS.
    ("MCDONALDS MILANO", "IT", "MCDONALDS"),
    ("CLIVATI 1969", "IT", None),
])
def test_nsi_key(description, country, key):
    assert nsi_key(description, country, lookup=_LIST) == key


def test_the_real_list_knows_the_founders_merchants():
    for description, key in [("TAMOIL GAGGIANO", "TAMOIL"), ("LIDL TREZZANO SUL NA", "LIDL"),
                             ("LEROY MERLIN CORSICO", "LEROY MERLIN"),
                             ("ESSELUNGA CORSICO", "ESSELUNGA")]:
        assert nsi_key(description, "IT") == key
    assert nsi_key("PIZZA", "IT") is None


@pytest.mark.parametrize("pattern, match_type, confidence", [
    ("TAMOIL", STARTS_WITH, KEY_KNOWN),
    ("CLIVATI", STARTS_WITH, KEY_CHECK),
    ("TAMOIL", "exact", None),
    ("TAMOIL", "contains", None),
])
def test_key_confidence(pattern, match_type, confidence):
    assert key_confidence(pattern, match_type, "IT", lookup=_LIST) == confidence


# ── The service ──────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    from services.settings_service import SettingsService
    SettingsService(eng).set("country", "IT")
    return eng


def _fake_model(monkeypatch, keys=None, fail=False):
    calls = []

    def _call(primary, system_prompt, user_prompt, json_schema, **kw):
        calls.append(user_prompt)
        if fail:
            raise RuntimeError("model down")
        import json
        items = json.loads(user_prompt.split("\n", 1)[1])
        return {"results": [{"idx": it["idx"], "key": (keys or {}).get(it["text"], "")}
                            for it in items] + [{"idx": 99, "key": "X"}]}, "fake"

    monkeypatch.setattr("core.llm_backends.call_with_fallback", _call)
    return calls


def test_the_list_spares_the_model(engine, monkeypatch):
    calls = _fake_model(monkeypatch)
    out = RuleService(engine).write_rules(["TAMOIL GAGGIANO", "LIDL TREZZANO SUL NA"],
                                          backend=object())
    assert out == {"TAMOIL GAGGIANO": ("TAMOIL", STARTS_WITH),
                   "LIDL TREZZANO SUL NA": ("LIDL", STARTS_WITH)}
    assert calls == []


def test_the_model_names_what_the_list_does_not_know(engine, monkeypatch):
    calls = _fake_model(monkeypatch, keys={
        "CLIVATI 1969 MILANO": "CLIVATI", "FATTURA 53": "", "AL CANTINONE VIA AGN": "CANTINONE",
        "BAR ROSSI": "BAR ROSSI SPA",
    })
    out = RuleService(engine).write_rules(
        ["CLIVATI 1969 MILANO", "FATTURA 53", "AL CANTINONE VIA AGN", "BAR ROSSI", "TAMOIL X"],
        backend=object(),
    )
    assert out == {
        "CLIVATI 1969 MILANO": ("CLIVATI", STARTS_WITH),
        "FATTURA 53": ("FATTURA 53", "exact"),
        "AL CANTINONE VIA AGN": ("AL CANTINONE", STARTS_WITH),
        "BAR ROSSI": ("BAR ROSSI", "exact"),          # the model added a word
        "TAMOIL X": ("TAMOIL", STARTS_WITH),          # from the list
    }
    assert len(calls) == 1 and "TAMOIL" not in calls[0]


def test_a_failing_model_leaves_the_exact_text(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    out = RuleService(engine).write_rules(["CLIVATI 1969 MILANO"], backend=object())
    assert out == {"CLIVATI 1969 MILANO": ("CLIVATI 1969 MILANO", "exact")}


def test_no_model_call_while_an_import_runs(engine, monkeypatch):
    from db.repository import create_import_job
    calls = _fake_model(monkeypatch, keys={"CLIVATI 1969 MILANO": "CLIVATI"})
    with get_session(engine) as s:
        create_import_job(s, n_files=1)
        s.commit()
    out = RuleService(engine).write_rules(["CLIVATI 1969 MILANO", "TAMOIL A"], backend=object())
    assert out == {"CLIVATI 1969 MILANO": ("CLIVATI 1969 MILANO", "exact"),
                   "TAMOIL A": ("TAMOIL", STARTS_WITH)}
    assert calls == []


# ── Applying rules ───────────────────────────────────────────────────────────

def _tx(session, tx_id, description, source="llm", category="Altro"):
    session.add(Transaction(
        id=tx_id, date="2026-08-01", amount=Decimal("-10"), currency="EUR",
        description=description, source_file="f.csv", doc_type="bank_statement",
        account_label="BancaX", tx_type="expense", to_review=False,
        category=category, subcategory="", category_source=source,
    ))


def test_the_ledger_finds_starts_with_and_run_all_keeps_hand_choices(engine):
    RuleService(engine).create_rule(pattern="TAMOIL", match_type=STARTS_WITH,
                                    category="Trasporti", subcategory="", priority=10)
    with get_session(engine) as s:
        _tx(s, "a", "TAMOIL GAGGIANO")
        _tx(s, "b", "TAMOIL ROZZANO", source="manual", category="Lavoro")
        _tx(s, "c", "TAMOILX")
        s.commit()
        assert sorted(t.id for t in get_transactions_by_rule_pattern(s, "TAMOIL", STARTS_WITH)) == ["a", "b"]
        n, _ = apply_all_rules_to_all_transactions(s, get_category_rules(s))
        s.commit()
        assert n == 1
        assert s.get(Transaction, "a").category == "Trasporti"
        assert s.get(Transaction, "b").category == "Lavoro"      # a one-off exception stays
        assert s.get(Transaction, "c").category == "Altro"


# ── The queue of rules to rewrite (spec 18) ────────────────────────────

def _tick(engine, description, category="Ristorazione"):
    """What a tick does: write the rule, queue it if the model was not there."""
    svc = RuleService(engine)
    pattern, match_type = svc.write_rules([description], backend=object(), queue=True)[description]
    svc.create_rule(pattern=pattern, match_type=match_type, category=category,
                    subcategory="", priority=10)
    return pattern, match_type


def test_a_rule_the_model_could_not_write_waits_and_then_sweeps(engine, monkeypatch):
    with get_session(engine) as s:
        _tx(s, "c1", "CLIVATI 1969 MILANO")
        _tx(s, "c2", "CLIVATI 1969")
        _tx(s, "c3", "CLIVATI 1969 RHO", source="manual", category="Lavoro")
        s.commit()
    _fake_model(monkeypatch, fail=True)
    assert _tick(engine, "CLIVATI 1969 MILANO") == ("CLIVATI 1969 MILANO", "exact")
    svc = RuleService(engine)
    assert svc.pending_rewrites() == ["CLIVATI 1969 MILANO"]

    assert svc.rewrite_pending(backend=object()) == []           # model still down
    assert svc.pending_rewrites() == ["CLIVATI 1969 MILANO"]

    _fake_model(monkeypatch, keys={"CLIVATI 1969 MILANO": "CLIVATI"})
    assert svc.rewrite_pending(backend=object()) == [("CLIVATI 1969 MILANO", "CLIVATI", 2)]
    assert svc.pending_rewrites() == []
    with get_session(engine) as s:
        assert [(r.pattern, r.match_type) for r in get_category_rules(s)] == [("CLIVATI", STARTS_WITH)]
        assert s.get(Transaction, "c2").category == "Ristorazione"     # swept
        assert s.get(Transaction, "c3").category == "Lavoro"           # hand choice stays


def test_the_queue_drops_what_has_no_more_reason_to_wait(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    _tick(engine, "BAR ROSSI MILANO")
    _tick(engine, "FATTURA 53")
    svc = RuleService(engine)
    svc.delete_rule(next(r.id for r in svc.get_rules() if r.pattern == "BAR ROSSI MILANO"))
    _fake_model(monkeypatch, keys={"FATTURA 53": ""})               # answered: no usable key
    assert svc.rewrite_pending(backend=object()) == []
    assert svc.pending_rewrites() == []
    assert [(r.pattern, r.match_type) for r in svc.get_rules()] == [("FATTURA 53", "exact")]


def test_known_merchants_and_forms_never_queue(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    _tick(engine, "TAMOIL GAGGIANO")                                    # from the list
    RuleService(engine).write_rules(["CLIVATI 1969"], backend=object())  # a form, not a tick
    assert RuleService(engine).pending_rewrites() == []


def test_two_spellings_rewritten_together_give_one_rule(engine, monkeypatch):
    _fake_model(monkeypatch, fail=True)
    _tick(engine, "CLIVATI 1969 MILANO")
    _tick(engine, "CLIVATI 1969")
    _fake_model(monkeypatch, keys={"CLIVATI 1969 MILANO": "CLIVATI", "CLIVATI 1969": "CLIVATI"})
    RuleService(engine).rewrite_pending(backend=object())
    assert [(r.pattern, r.match_type) for r in RuleService(engine).get_rules()] == [
        ("CLIVATI", STARTS_WITH),
    ]
