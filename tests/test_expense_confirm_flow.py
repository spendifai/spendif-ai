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
    CHECK,
    CHOOSE,
    CLOSED,
    CONFIRM_CATEGORY,
    CONFIRM_ONE,
    MAX_ROUNDS,
    PICK_LIST,
    PICK_NEAREST,
    SAVE_ASK,
    ASK_WHAT,
    WINDOW_DAYS,
    Candidate,
    ReceiptMatch,
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

    def __init__(self, movements=(), checked=(), model=None, questions=(), last=None, intent=None):
        self.movements = list(movements)
        self.last = last
        self.checked = list(checked)
        self.model = model
        self.questions = list(questions)
        self.searches = []
        self.confirmed = []
        self.always = []
        self.intent = intent          # what the model reads in an answer, by kind
        self.asked = []
        self.saved = []           # receipts saved for the next import
        self.receipts = []        # receipts checked against their movement
        self.cash = None          # the cash account, None while there is none
        self.cash_moves = []      # movements written in the cash account

    def outflows(self, low, high, date_from, date_to, checked):
        self.searches.append((low, high, date_from, date_to, checked))
        pool = self.checked if checked else self.movements
        return [c for c in pool if low <= c.amount <= high and date_from <= c.day <= date_to]

    def category_pairs(self):
        return PAIRS

    def last_date(self):
        return self.last

    def model_pick(self, text, labels):
        return self.model(text, labels) if self.model else None

    def model_intent(self, kind, text):
        self.asked.append((kind, text))
        return (self.intent or {}).get(kind)

    def confirm(self, tx_id, pair):
        self.confirmed.append((tx_id, pair))
        return self.questions if pair else []

    def always_yes(self, question):
        self.always.append(("yes", question))
        return "note"

    def always_no(self, question):
        self.always.append(("no", question))

    def save_receipt(self, day, amount, what, pair, cash):
        self.saved.append((day, amount, what, pair, cash))

    def cash_account(self):
        return self.cash

    def create_cash_account(self):
        self.cash = "Contanti"
        return self.cash

    def cash_movement(self, day, amount, what, pair, account):
        self.cash_moves.append((day, amount, what, pair, account))

    def confirm_receipt(self, receipt_id, tx_id, pair):
        self.receipts.append((receipt_id, tx_id, pair))
        return self.questions if pair else []


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
    msgs, state = _say(state, "il 23, 23,40", ledger)
    assert _keys(msgs) == ["expense.found_one"]        # the date said is not repeated
    assert msgs[0].params["name"] == "Esselunga"
    assert state.step == CONFIRM_ONE
    msgs, state = _say(state, "sì", ledger)
    assert ledger.confirmed == [("t1", None)]          # checked, category unchanged
    assert _keys(msgs) == ["expense.done"]             # and the next expense is listened to, not asked
    assert state.step == ASK_EXPENSE
    assert state.checked == 1


def test_a_date_other_than_the_one_said_is_the_news():
    """Spec 32, 2.5: the bot says only what the user does not know."""
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 23))])
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.on_day", "expense.found_one"]
    assert msgs[0].params["day"] == date(2026, 10, 23)


def test_the_window_is_three_days_each_side():
    assert WINDOW_DAYS == 3
    ledger = FakeLedger()
    _, state = start()
    _say(state, "il 12, 23,40", ledger)
    low, high, d_from, d_to, checked = ledger.searches[0]
    assert (low, high) == (Decimal("23.40"), Decimal("23.40"))
    assert (d_from, d_to) == (date(2026, 10, 9), date(2026, 10, 15))
    assert checked is False


def test_a_movement_outside_the_window_is_offered_as_another_day():
    """Spec 32, 4.2 C 1: the date remembered is wrong more often than the
    amount; the same amount within a month is offered, never confirmed."""
    ledger = FakeLedger([_c("t1", "15.10", date(2026, 10, 13))])
    _, state = start()
    msgs, state = _say(state, "15,10 23 ottobre", ledger)
    assert _keys(msgs) == ["expense.on_day", "expense.found_one"]
    assert msgs[0].params["day"] == date(2026, 10, 13)
    assert state.step == CONFIRM_ONE and state.chosen.tx_id == "t1"


def test_several_on_other_days_are_a_choice():
    ledger = FakeLedger([_c("t1", "15.10", date(2026, 10, 13)),
                         _c("t2", "15.10", date(2026, 10, 2), name="Bar Sole")])
    _, state = start()
    msgs, state = _say(state, "15,10 23 ottobre", ledger)
    assert _keys(msgs) == ["expense.found_many"]     # each line carries its date
    assert state.step == CHOOSE
    assert [c.tx_id for c in state.candidates] == ["t1", "t2"]   # the closest first


def test_on_another_day_but_already_checked_is_said():
    ledger = FakeLedger(checked=[_c("t1", "15.10", date(2026, 10, 13))])
    _, state = start()
    msgs, state = _say(state, "15,10 23 ottobre", ledger)
    assert _keys(msgs) == ["expense.already_checked"]
    assert msgs[0].params["day"] == date(2026, 10, 13)


def test_more_than_a_month_away_is_not_offered():
    ledger = FakeLedger([_c("t1", "15.10", date(2026, 9, 1))])
    _, state = start()
    msgs, state = _say(state, "15,10 il 12 ottobre", ledger)
    assert _keys(msgs) == ["expense.not_found", "expense.save_ask"]


def test_a_date_after_the_last_movement_is_not_there_yet():
    """Spec 32, 4.2 C 2: the statements stop on the 18th, the expense of
    the 23rd cannot be there yet."""
    ledger = FakeLedger(last=date(2026, 10, 18))
    _, state = start()
    msgs, state = _say(state, "15,10 23 ottobre", ledger)
    assert _keys(msgs) == ["expense.not_yet", "expense.save_ask"]
    assert msgs[0].params["last"] == date(2026, 10, 18)
    assert state.step == SAVE_ASK


def test_a_date_covered_by_the_data_is_not_found():
    ledger = FakeLedger(last=date(2026, 10, 18))
    _, state = start()
    msgs, _ = _say(state, "15,10 il 12 ottobre", ledger)
    assert _keys(msgs) == ["expense.not_found", "expense.save_ask"]


def test_a_no_is_outcome_c():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.not_found", "expense.save_ask"]
    assert state.step == SAVE_ASK
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
    assert _keys(msgs) == ["expense.found_many"]
    assert [c.tx_id for c in msgs[0].params["items"]] == ["t1", "t2"]   # nearest to the day first
    assert state.step == CHOOSE
    msgs, state = _say(state, "bar centrale", ledger)
    assert _keys(msgs) == ["expense.found_one"]        # chosen from dated lines: no date added
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
    assert _keys(msgs) == ["expense.not_found", "expense.save_ask"]


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
    assert _keys(msgs) == ["expense.found_near"]
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
    assert _keys(msgs) == ["expense.not_found", "expense.save_ask"]
    assert msgs[0].params["amount"] == Decimal("23.40")
    assert msgs[0].params["day"] == date(2026, 10, 24)
    assert state.step == SAVE_ASK
    msgs, state = _say(state, "ieri, 23,50", FakeLedger([_c("t9", "23.50", date(2026, 10, 24))]))
    assert _keys(msgs) == ["expense.found_one"]


def test_already_checked_is_said():
    ledger = FakeLedger(checked=[_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.already_checked"]
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
    assert _keys(msgs) == ["expense.done"]
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
    assert _keys(msgs) == ["expense.done", "expense.always"]
    assert state.step == ALWAYS
    return state


def test_always_yes_writes_the_rule():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "sì", ledger)
    assert ledger.always == [("yes", _Q)]
    assert _keys(msgs) == ["expense.rule_done"]
    assert msgs[0].note == "note"
    assert state.step == ASK_EXPENSE


def test_always_no_is_remembered():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "no", ledger)
    assert ledger.always == [("no", _Q)]
    assert _keys(msgs) == ["expense.always_kept"]


def test_always_unclear_asks_again():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "mah", ledger)
    assert _keys(msgs) == ["expense.always"]
    assert ledger.always == []


def test_a_check_alone_never_asks_always():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, _ = _say(state, "sì", ledger)
    assert "expense.always" not in _keys(msgs)


def test_never_creates_a_movement_but_for_cash():
    """Mai un movimento inventato (spec 32, 2.4): the only ports that write a
    movement or an account are the cash ones, used after the user said the
    expense was cash (4.4)."""
    writing = {name for name in dir(FakeLedger) if not name.startswith("_")
               and name.startswith(("create", "add", "insert", "cash_movement"))}
    assert writing == {"create_cash_account", "cash_movement"}


# ── 4.6 Closing ──────────────────────────────────────────────────────────────

def _checked_two(ledger):
    _, state = start()
    for text in ("il 24, 23,40", "sì", "il 24, 9,90", "sì"):
        _, state = _say(state, text, ledger)
    assert state.checked == 2 and len(ledger.confirmed) == 2
    return state


def _ledger_two():
    return FakeLedger([_c("t1", "23.40", date(2026, 10, 24)),
                       _c("t2", "9.90", date(2026, 10, 24), name="Bar Sole")])


def test_closing_says_how_many_were_checked():
    ledger = _ledger_two()
    state = _checked_two(ledger)
    msgs, state = _say(state, "ho finito", ledger)
    assert _keys(msgs) == ["expense.closed"]
    assert msgs[0].params["n"] == 2
    assert state.step == CLOSED


def test_a_bare_no_closes_only_while_waiting_for_an_expense():
    ledger = _ledger_two()
    state = _checked_two(ledger)
    msgs, state = _say(state, "no grazie", ledger)
    assert state.step == CLOSED
    # halfway, the same "no" answers the question: "not this one"
    _, state = start()
    _, state = _say(state, "il 24, 23,40", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.not_found", "expense.save_ask"]
    assert state.step == SAVE_ASK


def test_a_sentence_with_a_date_or_amount_is_an_expense():
    ledger = _ledger_two()
    _, state = start()
    msgs, state = _say(state, "basta, il 24 23,40", ledger)
    assert _keys(msgs) == ["expense.found_one"]
    assert state.step == CONFIRM_ONE


def test_an_expense_halfway_is_not_written_and_is_said():
    ledger = _ledger_two()
    _, state = start()
    _, state = _say(state, "il 24, 23,40", ledger)
    msgs, state = _say(state, "basta", ledger)
    assert _keys(msgs) == ["expense.left", "expense.closed_none"]
    assert msgs[0].params["amount"] == Decimal("23.40")
    assert ledger.confirmed == []
    assert state.step == CLOSED


def test_closing_at_always_leaves_the_question_unanswered():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "fine", ledger)
    assert _keys(msgs) == ["expense.closed_one"]       # already checked: nothing left out
    assert ledger.always == []                         # neither a rule nor a refusal
    assert state.step == CLOSED


def test_after_closing_nothing_is_heard():
    ledger = _ledger_two()
    _, state = start()
    _, state = _say(state, "fine", ledger)
    msgs, state = _say(state, "il 24, 23,40", ledger)
    assert msgs == [] and state.step == CLOSED
    assert ledger.searches == []


def test_the_count_survives_an_expense_not_found():
    ledger = _ledger_two()
    state = _checked_two(ledger)
    _, state = _say(state, "il 3 ottobre, 99 euro", ledger)
    assert state.checked == 2


# ── 4.7 Correcting the last expense ─────────────────────────────────────────

def test_the_last_category_can_be_corrected_after_done():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    _, state = _say(state, "il 24, 23,40", ledger)
    _, state = _say(state, "confermato", ledger)
    assert ledger.confirmed == [("t1", None)]
    msgs, state = _say(state, "cambia categoria", ledger)
    assert _keys(msgs) == ["expense.correct"]
    assert msgs[0].params["name"] == "Esselunga"
    assert state.step == ASK_CATEGORY and state.chosen.tx_id == "t1"
    _, state = _say(state, "farmaci", ledger)
    msgs, state = _say(state, "sì", ledger)
    assert ledger.confirmed[-1] == ("t1", ("Salute", "Farmaci"))
    assert _keys(msgs) == ["expense.done"]
    assert state.checked == 1                   # the same movement: not one more


def test_a_second_correction_compares_with_the_category_now():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))])
    _, state = start()
    for text in ("il 24, 23,40", "sì", "correggi", "farmaci", "sì", "ho sbagliato"):
        _, state = _say(state, text, ledger)
    assert state.chosen.pair == ("Salute", "Farmaci")


def test_a_correction_with_an_amount_is_a_new_expense():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24)),
                         _c("t2", "9.90", date(2026, 10, 24), name="Bar Sole")])
    _, state = start()
    _, state = _say(state, "il 24, 23,40", ledger)
    _, state = _say(state, "sì", ledger)
    msgs, state = _say(state, "ho sbagliato, il 24 9,90", ledger)
    assert _keys(msgs) == ["expense.found_one"] and state.chosen.tx_id == "t2"


def test_nothing_to_correct_before_a_confirmation():
    ledger = FakeLedger()
    _, state = start()
    msgs, state = _say(state, "cambia categoria", ledger)
    assert _keys(msgs) == ["expense.ask_again"]


def test_a_correction_at_always_leaves_the_question_unanswered():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q])
    state = _to_always(ledger)
    msgs, state = _say(state, "no aspetta, ho sbagliato", ledger)
    assert _keys(msgs) == ["expense.correct"]
    assert ledger.always == []
    assert state.chosen.pair == ("Casa", "Arredamento")


# ── 4.8 The model proposes, the user confirms ───────────────────────────────

def _found_one(ledger):
    _, state = start()
    _, state = _say(state, "il 24, 23,40", ledger)
    assert state.step == CONFIRM_ONE
    return state


def test_an_answer_the_words_understand_never_reaches_the_model():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"found": "no"})
    _, _ = _say(_found_one(ledger), "confermato", ledger)
    assert ledger.asked == [] and ledger.confirmed == [("t1", None)]


def test_the_model_proposes_and_a_yes_does_it():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"found": "yes"})
    msgs, state = _say(_found_one(ledger), "mi sa di si", ledger)
    assert ledger.asked == [("found", "mi sa di si")]
    assert _keys(msgs) == ["expense.check.yes"] and state.step == CHECK
    assert ledger.confirmed == []                     # nothing written on the model's word
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.done"] and ledger.confirmed == [("t1", None)]


def test_a_no_to_what_the_model_understood_asks_again():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"found": "yes"})
    _, state = _say(_found_one(ledger), "boh", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.found_one"] and state.step == CONFIRM_ONE
    assert ledger.confirmed == []


def test_other_words_after_the_check_answer_the_question_of_before():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"found": "yes"})
    _, state = _say(_found_one(ledger), "boh", ledger)
    msgs, state = _say(state, "sì ma la categoria è un'altra", ledger)
    assert _keys(msgs) == ["expense.ask_category"]


def test_the_model_reads_a_closing():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"waiting": "close"})
    state = _found_one(ledger)
    _, state = _say(state, "sì", ledger)
    msgs, state = _say(state, "lasciamo perdere per oggi", ledger)
    assert _keys(msgs) == ["expense.check.close"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.closed_one"] and state.step == CLOSED


def test_the_model_reads_a_correction_only_with_something_to_correct():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"waiting": "correct"})
    _, state = start()
    msgs, state = _say(state, "la categoria di prima non andava", ledger)
    assert _keys(msgs) == ["expense.ask_again"]       # nothing confirmed yet
    state = _found_one(ledger)
    _, state = _say(state, "sì", ledger)
    msgs, state = _say(state, "la categoria di prima non andava", ledger)
    assert _keys(msgs) == ["expense.check.correct"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.correct"] and state.step == ASK_CATEGORY


def test_unclear_or_silent_model_leaves_the_flow_as_without_it():
    for intent in ({"found": "unclear"}, None):
        ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent=intent)
        msgs, state = _say(_found_one(ledger), "boh", ledger)
        assert _keys(msgs) == ["expense.yes_no"] and state.step == CONFIRM_ONE


def test_an_intent_out_of_place_is_ignored():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], intent={"found": "correct"})
    msgs, _ = _say(_found_one(ledger), "boh", ledger)
    assert _keys(msgs) == ["expense.yes_no"]


def test_the_model_at_always():
    ledger = FakeLedger([_c("t1", "23.40", date(2026, 10, 24))], questions=[_Q], intent={"always": "no"})
    state = _to_always(ledger)
    msgs, state = _say(state, "macche", ledger)
    assert _keys(msgs) == ["expense.check.no_rule"]       # "Solo questa?", not "Non e' questa?"
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.always_kept"] and ledger.always == [("no", _Q)]


# ── 4.4bis The receipt saved for the next import ────────────────────────────

def test_a_receipt_not_there_yet_is_saved_with_what_and_category():
    ledger = FakeLedger(last=date(2026, 10, 18))
    _, state = start()
    msgs, state = _say(state, "ieri, 23,40", ledger)
    assert _keys(msgs) == ["expense.not_yet", "expense.save_ask"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.ask_cash"]
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.ask_what"] and state.step == ASK_WHAT
    msgs, state = _say(state, "spesa supermercato", ledger)       # the description proposes the category
    assert _keys(msgs) == ["expense.propose_category"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.receipt_saved"]
    assert ledger.saved == [(date(2026, 10, 24), Decimal("23.40"), "spesa supermercato",
                             ("Alimentari", "Spesa supermercato"), False)]
    assert ledger.confirmed == [] and state.step == ASK_EXPENSE and state.checked == 0


def test_a_description_that_names_no_category_asks_for_it():
    ledger = FakeLedger()
    _, state = start()
    _, state = _say(state, "il 12 ottobre, 9 euro", ledger)
    _, state = _say(state, "ok", ledger)
    _, state = _say(state, "sì", ledger)                            # in cash
    msgs, state = _say(state, "zzz", ledger)
    assert _keys(msgs) == ["expense.ask_category"] and state.step == ASK_CATEGORY
    msgs, state = _say(state, "farmaci", ledger)
    assert _keys(msgs) == ["expense.propose_category"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.ask_cash_account"]       # no cash account yet
    msgs, state = _say(state, "no", ledger)
    assert ledger.saved[0][2:] == ("zzz", ("Salute", "Farmaci"), True)
    assert _keys(msgs) == ["expense.receipt_saved_cash"]


def test_a_no_does_not_save_and_asks_again():
    ledger = FakeLedger()
    _, state = start()
    _, state = _say(state, "ieri, 23,40", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.ask"] and state.step == ASK_EXPENSE
    assert ledger.saved == []


def test_a_new_expense_instead_of_an_answer_is_searched():
    ledger = FakeLedger([_c("t9", "23.50", date(2026, 10, 24))])
    _, state = start()
    _, state = _say(state, "ieri, 99 euro", ledger)
    assert state.step == SAVE_ASK
    msgs, state = _say(state, "ieri, 23,50", ledger)
    assert _keys(msgs) == ["expense.found_one"] and ledger.saved == []


# ── 4.4bis The receipts saved, checked at the start ─────────────────────────

def _r(rid, amount, day, what, pair, *cands):
    return ReceiptMatch(receipt_id=rid, day=day, amount=Decimal(amount), what=what, pair=pair,
                        candidates=tuple(cands))


_BAR = ("Ristorazione", "Bar e caffe")
_NEWS = ("Svago e tempo libero", "Hobby")


def test_no_receipts_no_question():
    msgs, state = start([])
    assert _keys(msgs) == ["expense.ask"] and state.step == ASK_EXPENSE


def test_a_receipt_with_one_movement_is_checked_after_a_yes():
    ledger = FakeLedger()
    bar = _c("t1", "2", date(2026, 10, 24), name="Bar Centrale", cat=None, sub=None)
    msgs, state = start([_r(1, "2", date(2026, 10, 24), "caffè", _BAR, bar)])
    assert _keys(msgs) == ["expense.receipts_found_one"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.review_one"]
    assert (msgs[0].params["what"], msgs[0].params["name"]) == ("caffè", "Bar Centrale")
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.done", "expense.ask"]
    assert ledger.receipts == [(1, "t1", _BAR)]
    assert state.step == ASK_EXPENSE and state.checked == 1


def test_the_same_category_checks_the_movement_only():
    ledger = FakeLedger()
    bar = _c("t1", "2", date(2026, 10, 24), name="Bar Centrale", cat=_BAR[0], sub=_BAR[1])
    _, state = start([_r(1, "2", date(2026, 10, 24), "caffè", _BAR, bar)])
    _, state = _say(state, "sì", ledger)
    _, state = _say(state, "sì", ledger)
    assert ledger.receipts == [(1, "t1", None)]


def test_two_equal_expenses_are_told_apart_by_the_users_words():
    """Two euro of coffee and two of newspaper on the same day (founder,
    2026-10-26): each receipt shows its own words, the user picks."""
    ledger = FakeLedger()
    day = date(2026, 10, 24)
    bar = _c("t1", "2", day, name="Bar Centrale", cat=None, sub=None)
    news = _c("t2", "2", day, name="Edicola Piazza", cat=None, sub=None)
    _, state = start([_r(1, "2", day, "caffè", _BAR, bar, news),
                      _r(2, "2", day, "giornale", _NEWS, bar, news)])
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.review_many"] and msgs[0].params["what"] == "caffè"
    msgs, state = _say(state, "bar centrale", ledger)
    assert ledger.receipts == [(1, "t1", _BAR)]
    # the bar is taken: the newspaper has one movement left
    assert _keys(msgs) == ["expense.done", "expense.review_one"]
    assert (msgs[1].params["what"], msgs[1].params["name"]) == ("giornale", "Edicola Piazza")
    msgs, state = _say(state, "sì", ledger)
    assert ledger.receipts == [(1, "t1", _BAR), (2, "t2", _NEWS)] and state.checked == 2


def test_a_no_keeps_the_receipt_waiting_and_goes_on():
    ledger = FakeLedger()
    bar = _c("t1", "2", date(2026, 10, 24), name="Bar Centrale", cat=None, sub=None)
    shop = _c("t3", "9", date(2026, 10, 24), name="Ferramenta", cat=None, sub=None)
    _, state = start([_r(1, "2", date(2026, 10, 24), "caffè", _BAR, bar),
                      _r(2, "9", date(2026, 10, 24), "viti", ("Casa", "Altro casa"), shop)])
    _, state = _say(state, "sì", ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.review_one"] and msgs[0].params["what"] == "viti"
    assert ledger.receipts == []


def test_no_to_the_review_goes_to_the_usual_question():
    ledger = FakeLedger()
    bar = _c("t1", "2", date(2026, 10, 24), name="Bar Centrale", cat=None, sub=None)
    _, state = start([_r(1, "2", date(2026, 10, 24), "caffè", _BAR, bar)])
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.ask"] and state.step == ASK_EXPENSE and ledger.receipts == []


def test_a_category_changed_by_a_receipt_asks_always_like_this_then_goes_on():
    """The receipt's category is not discussed, but a change of the
    movement's is a change by hand like any other (founder, 2026-10-26)."""
    q = {"n": 3, "description": "Bar Centrale", "category": _BAR[0], "subcategory": _BAR[1]}
    ledger = FakeLedger(questions=[q])
    bar = _c("t1", "2", date(2026, 10, 24), name="Bar Centrale")
    shop = _c("t3", "9", date(2026, 10, 24), name="Ferramenta", cat="Casa", sub="Altro casa")
    _, state = start([_r(1, "2", date(2026, 10, 24), "caffè", _BAR, bar),
                      _r(2, "9", date(2026, 10, 24), "viti", ("Casa", "Altro casa"), shop)])
    _, state = _say(state, "sì", ledger)
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.done", "expense.always"] and state.step == ALWAYS
    msgs, state = _say(state, "sì", ledger)
    assert ledger.always == [("yes", q)]
    assert _keys(msgs) == ["expense.rule_done", "expense.review_one"] and msgs[1].params["what"] == "viti"
    msgs, state = _say(state, "sì", ledger)
    assert ledger.receipts[-1] == (2, "t3", None)          # same category: no question
    assert _keys(msgs) == ["expense.done", "expense.ask"] and state.checked == 2


# ── 4.4 Cash: a movement of the cash account ────────────────────────────────

def _to_cash(ledger):
    _, state = start()
    _, state = _say(state, "ieri, 4 euro", ledger)
    _, state = _say(state, "sì", ledger)
    _, state = _say(state, "sì", ledger)                      # in cash
    _, state = _say(state, "farmaci", ledger)
    return _say(state, "sì", ledger)


def test_cash_becomes_a_movement_of_the_cash_account():
    ledger = FakeLedger()
    ledger.cash = "Portafoglio"
    msgs, state = _to_cash(ledger)
    assert _keys(msgs) == ["expense.cash_recorded"] and msgs[0].params["account"] == "Portafoglio"
    assert ledger.cash_moves == [(date(2026, 10, 24), Decimal("4"), "farmaci", ("Salute", "Farmaci"), "Portafoglio")]
    assert ledger.saved == [] and state.step == ASK_EXPENSE and state.checked == 1


def test_without_a_cash_account_the_bot_offers_to_create_it():
    ledger = FakeLedger()
    msgs, state = _to_cash(ledger)
    assert _keys(msgs) == ["expense.ask_cash_account"]
    msgs, state = _say(state, "sì", ledger)
    assert _keys(msgs) == ["expense.cash_recorded"] and ledger.cash == "Contanti"
    assert ledger.cash_moves[0][4] == "Contanti"


def test_no_cash_account_keeps_the_receipt_waiting():
    ledger = FakeLedger()
    _, state = _to_cash(ledger)
    msgs, state = _say(state, "no", ledger)
    assert _keys(msgs) == ["expense.receipt_saved_cash"]
    assert ledger.saved[0][4] is True and ledger.cash_moves == []
