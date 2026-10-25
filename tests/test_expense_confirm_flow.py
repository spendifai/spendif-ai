"""The fixed flow of turns that confirms an expense (spec 32, 2.1 and 4).

State in, user text in: bot reply and new state out. The ledger, the
taxonomy and the model are a fake here: the flow is tested without a
database and without a page.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from core.expense_confirm import (
    ALWAYS,
    ASK_CATEGORY,
    ASK_EXPENSE,
    CHOOSE,
    CONFIRM_CATEGORY,
    CONFIRM_ONE,
    MAX_ROUNDS,
    PICK_LIST,
    PICK_NEAREST,
    WINDOW_DAYS,
    Candidate,
    start,
    turn,
)

TODAY = date(2026, 10, 25)
PAIRS = [
    ("Casa", "Manutenzione e riparazioni"), ("Casa", "Arredamento"), ("Casa", "Altro casa"),
    ("Alimentari", "Spesa supermercato"), ("Salute", "Farmaci"), ("Salute", "Dentista"),
    ("Trasporti", "Pedaggio"), ("Trasporti", "Parcheggio / ZTL"), ("Svago e tempo libero", "Hobby"),
    ("Abbigliamento", "Calzature"), ("Comunicazioni", "Telefonia mobile"),
    ("Animali domestici", "Veterinario"), ("Regali e donazioni", "Regali"),
]


def _c(tx_id, amount, day, name="Esselunga", cat="Alimentari", sub="Spesa supermercato"):
    return Candidate(tx_id=tx_id, day=day, amount=Decimal(amount), counterpart=name,
                     category=cat, subcategory=sub)


class FakeLedger:
    """The ports of the flow: what it reads and writes, recorded."""

    def __init__(self, movements=(), checked=(), model=None, questions=()):
        self.movements = list(movements)
        self.checked = list(checked)
        self.model = model
        self.questions = list(questions)
        self.searches = []
        self.confirmed = []
        self.always = []

    def outflows(self, low, high, date_from, date_to, checked):
        self.searches.append((low, high, date_from, date_to, checked))
        pool = self.checked if checked else self.movements
        return [c for c in pool if low <= c.amount <= high and date_from <= c.day <= date_to]

    def category_pairs(self):
        return PAIRS

    def model_pick(self, text, labels):
        return self.model(text, labels) if self.model else None

    def confirm(self, tx_id, pair):
        self.confirmed.append((tx_id, pair))
        return self.questions if pair else []

    def always_yes(self, question):
        self.always.append(("yes", question))
        return "note"

    def always_no(self, question):
        self.always.append(("no", question))


def _say(state, text, ledger):
    return turn(state, text, ledger, TODAY)


def _keys(msgs):
    return [m.key for m in msgs]


def test_the_first_question():
    msgs, state = start()
    assert _keys(msgs) == ["expense.ask"]
    assert state.step == ASK_EXPENSE


# ── 4.2 Outcome A ────────────────────────────────────────────────────────────

def test_a_one_found_and_confirmed():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 23))])
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.found_one"]
    assert msgs[0].params["name"] == "Esselunga"
    assert state.step == CONFIRM_ONE
    msgs, state = _say(state, "sì", ledger)
    assert ledger.confirmed == [("t1", None)]          # checked, category unchanged
    assert _keys(msgs) == ["expense.done", "expense.next"]
    assert state.step == ASK_EXPENSE


def test_the_window_is_three_days_each_side():
    assert WINDOW_DAYS == 3
    ledger = FakeLedger()
    _, state = start()
    _say(state, "il 12, 23,40", ledger)
    low, high, d_from, d_to, checked = ledger.searches[0]
    assert (low, high) == (Decimal("23.40"), Decimal("23.40"))
    assert (d_from, d_to) == (date(2026, 10, 9), date(2026, 10, 15))
    assert checked is False


def test_a_movement_outside_the_window_is_not_found():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24) - timedelta(days=4))])
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.not_found"]


def test_a_no_is_outcome_c():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.not_found"]
    assert state.step == ASK_EXPENSE
    assert ledger.confirmed == []


def test_an_unclear_answer_asks_again():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, state2 = _say(state, "forse", ledger)
    assert _keys(msgs) == ["expense.yes_no"]
    assert state2 == state


def test_a_movement_without_category_asks_for_one():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24), cat=None, sub=None)])
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.found_one_nocat"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.ask_category"]
    assert state.step == ASK_CATEGORY


# ── 4.2 Outcome B ────────────────────────────────────────────────────────────

def _two():
    return FakeLedger([
        _c("t1", "23.40", date(2026, 10, 12)),
        _c("t2", "23.40", date(2026, 10, 13), name="Bar Centrale", cat="Alimentari"),
    ])


def test_b_several_chosen_by_counterpart():
    ledger = _two()
    _, state = start()
    msgs, state = _say(state, "il 12, 23 e 40", ledger)
    assert _keys(msgs) == ["expense.found_many", "expense.which"]
    assert [c.tx_id for c in msgs[0].params["items"]] == ["t1", "t2"]   # nearest to the day first
    assert state.step == CHOOSE
    msgs, state = _say(state, "bar centrale", ledger)
    assert _keys(msgs) == ["expense.found_one"]
    assert state.chosen.tx_id == "t2"
    _say(state, "sì", ledger)
    assert ledger.confirmed == [("t2", None)]


def test_b_chosen_by_date():
    ledger = _two()
    _, state = start()
    _, state = _say(state, "il 12, 23 e 40", ledger)
    _, state = _say(state, "quella del 13", ledger)
    assert state.chosen.tx_id == "t2"


def test_b_none_of_them_is_outcome_c():
    ledger = _two()
    _, state = start()
    _, state = _say(state, "il 12, 23 e 40", ledger)
    msgs, state = _say(state, "nessuna", ledger)
    assert _keys(msgs) == ["expense.not_found"]


def test_b_an_unclear_choice_asks_again():
    ledger = _two()
    _, state = start()
    _, state = _say(state, "il 12, 23 e 40", ledger)
    msgs, state = _say(state, "boh", ledger)
    assert _keys(msgs) == ["expense.pick_again"]
    assert state.step == CHOOSE


# ── 4.1 An amount said without cents ─────────────────────────────────────────

def test_without_cents_the_exact_euro_first():
    ledger = FakeLedger([_c("t1", "23.00", date(2026, 10, 24)), _c("t2", "23.40", date(2026, 10, 24))])
    _, state = start()
    msgs, state = _say(state, "ieri 23", ledger)
    assert _keys(msgs) == ["expense.found_one"]
    assert state.chosen.tx_id == "t1"


def test_without_cents_then_the_cents_of_that_euro_always_as_a_choice():
    ledger = FakeLedger([_c("t2", "23.40", date(2026, 10, 24))])
    _, state = start()
    msgs, state = _say(state, "ieri 23", ledger)
    # one movement only, still offered as a choice: never confirmed by itself
    assert _keys(msgs) == ["expense.found_near", "expense.which"]
    assert state.step == CHOOSE
    assert [s[:2] for s in ledger.searches] == [(Decimal("23"), Decimal("23")),
                                               (Decimal("23"), Decimal("23.99"))]
    msgs, state = _say(state, "sì", ledger)
    assert state.chosen.tx_id == "t2"
    assert _keys(msgs) == ["expense.found_one"]


# ── 4.2 Outcome C ────────────────────────────────────────────────────────────

def test_c_not_found_offers_to_try_again():
    ledger = FakeLedger()
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.not_found"]
    assert msgs[0].params["amount"] == Decimal("23.40")
    assert msgs[0].params["day"] == date(2026, 10, 24)
    assert state.step == ASK_EXPENSE
    msgs, state = _say(state, "ieri, 23,50", FakeLedger([_c("t9", "23.50", date(2026, 10, 24))]))
    assert _keys(msgs) == ["expense.found_one"]


def test_already_checked_is_said():
    ledger = FakeLedger(checked=[_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.already_checked", "expense.next"]
    assert state.step == ASK_EXPENSE


# ── 4.1 Half a sentence ──────────────────────────────────────────────────────

def test_the_missing_half_is_asked_and_the_other_kept():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    msgs, state = _say(state, "23,40", ledger)
    assert _keys(msgs) == ["expense.ask_date"]
    msgs, state = _say(state, "ieri", ledger)
    assert _keys(msgs) == ["expense.found_one"]

    _, state = start()
    msgs, state = _say(state, "ieri", ledger)
    assert _keys(msgs) == ["expense.ask_amount"]
    msgs, state = _say(state, "23 e 40", ledger)
    assert _keys(msgs) == ["expense.found_one"]


def test_a_bare_number_after_the_amount_is_the_day():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 12))])
    _, state = start()
    _, state = _say(state, "23,40", ledger)
    msgs, state = _say(state, "12", ledger)
    assert _keys(msgs) == ["expense.found_one"]


def test_nothing_understood():
    _, state = start()
    msgs, state = _say(state, "buongiorno", FakeLedger())
    assert _keys(msgs) == ["expense.ask_again"]


def test_unsure_values_are_repeated():
    ledger = FakeLedger([_c("t1", "23.00", date(2026, 10, 24))])
    _, state = start()
    msgs, _ = _say(state, "ieri 23 40 7", ledger)
    assert _keys(msgs)[0] == "expense.understood"


# ── 4.3 The category ─────────────────────────────────────────────────────────

def _at_category(ledger, text="sì, ma la categoria è un'altra"):
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, state = _say(state, text, ledger)
    assert _keys(msgs) == ["expense.ask_category"]
    return state


def test_category_direct_match_confirmed():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    state = _at_category(ledger)
    msgs, state = _say(state, "manutenzione casa", ledger)
    assert _keys(msgs) == ["expense.propose_category"]
    assert state.step == CONFIRM_CATEGORY
    msgs, state = _say(state, "sì", ledger)
    assert ledger.confirmed == [("t1", ("Casa", "Manutenzione e riparazioni"))]
    assert _keys(msgs) == ["expense.done", "expense.next"]
    assert msgs[0].params["cat"] == ("Casa", "Manutenzione e riparazioni")


def test_the_same_category_again_is_only_a_check():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    state = _at_category(ledger)
    _, state = _say(state, "supermercato", ledger)
    _say(state, "sì", ledger)
    assert ledger.confirmed == [("t1", None)]


def test_the_model_finds_what_the_words_do_not():
    asked = []

    def model(text, labels):
        asked.append(text)
        return labels.index("Casa / Manutenzione e riparazioni")

    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], model=model)
    state = _at_category(ledger)
    msgs, state = _say(state, "ho aggiustato il rubinetto", ledger)
    assert asked == ["ho aggiustato il rubinetto"]
    assert _keys(msgs) == ["expense.propose_category"]
    assert state.proposal == ("Casa", "Manutenzione e riparazioni")


def test_the_model_is_not_asked_when_the_words_are_enough():
    asked = []
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))],
                        model=lambda text, labels: asked.append(text))
    state = _at_category(ledger)
    _say(state, "farmaci", ledger)
    assert asked == []


def test_a_model_answer_out_of_the_list_is_ignored():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], model=lambda text, labels: 99)
    state = _at_category(ledger)
    msgs, state = _say(state, "spesa per la casa", ledger)
    assert _keys(msgs) == ["expense.nearest"]


def test_a_refused_proposal_gives_the_three_nearest():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    state = _at_category(ledger)
    _, state = _say(state, "manutenzione casa", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.nearest"]
    assert state.step == PICK_NEAREST
    assert ("Casa", "Manutenzione e riparazioni") not in state.nearest
    assert len(msgs[0].params["items"]) == 3
    second = state.nearest[1]
    msgs, state = _say(state, "2", ledger)
    assert ledger.confirmed == [("t1", second)]
    assert msgs[0].params["cat"] == second


def test_three_rounds_then_the_whole_list():
    assert MAX_ROUNDS == 3
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    state = _at_category(ledger)
    for round_ in range(MAX_ROUNDS):
        msgs, state = _say(state, "spesa per la casa", ledger)
        assert _keys(msgs) == ["expense.nearest"], round_
        msgs, state = _say(state, "nessuna", ledger)
        if round_ < MAX_ROUNDS - 1:
            assert _keys(msgs) == ["expense.other_words"]
            assert state.step == ASK_CATEGORY
    assert _keys(msgs) == ["expense.pick_list"]
    assert state.step == PICK_LIST
    assert state.options == PAIRS
    msgs, state = _say(state, "Salute / Farmaci", ledger)
    assert ledger.confirmed == [("t1", ("Salute", "Farmaci"))]


def test_the_whole_list_asks_again_until_a_choice():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    state = _at_category(ledger)
    for _ in range(MAX_ROUNDS):
        _, state = _say(state, "spesa per la casa", ledger)
        _, state = _say(state, "no", ledger)
    msgs, state = _say(state, "boh", ledger)
    assert _keys(msgs) == ["expense.pick_list"]
    assert ledger.confirmed == []


def test_new_words_instead_of_a_choice_are_a_new_round():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    state = _at_category(ledger)
    _, state = _say(state, "spesa per la casa", ledger)
    msgs, state = _say(state, "farmaci", ledger)
    assert _keys(msgs) == ["expense.propose_category"]
    assert state.rounds == 1


# ── 4.5 "Sempre cosi'?" ──────────────────────────────────────────────────────

_Q = {"description": "ESSELUNGA", "category": "Casa", "subcategory": "Arredamento",
      "direction": "out", "n": 3}


def _to_always(ledger):
    state = _at_category(ledger)
    _, state = _say(state, "arredamento", ledger)
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.done", "ledger.always.ask"]
    assert state.step == ALWAYS
    return state


def test_always_yes_writes_the_rule():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "sì", ledger)
    assert ledger.always == [("yes", _Q)]
    assert _keys(msgs) == ["ledger.always.done", "expense.next"]
    assert msgs[0].note == "note"
    assert state.step == ASK_EXPENSE


def test_always_no_is_remembered():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "no", ledger)
    assert ledger.always == [("no", _Q)]
    assert _keys(msgs) == ["expense.always_kept", "expense.next"]


def test_always_unclear_asks_again():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "mah", ledger)
    assert _keys(msgs) == ["ledger.always.ask"]
    assert ledger.always == []


def test_a_check_alone_never_asks_always():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, _ = _say(state, "sì", ledger)
    assert "ledger.always.ask" not in _keys(msgs)


def test_never_creates_a_movement():
    """Mai un movimento inventato (spec 32, 2.4): the flow has no port to do it."""
    assert not any(name.startswith(("create", "add", "insert"))
                   for name in dir(FakeLedger) if not name.startswith("_"))
