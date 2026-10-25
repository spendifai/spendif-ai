"""The fixed flow that confirms an expense (spec 32, 4): a state machine of
turns, not a free agent (spec 32, 2.1).

`turn(state, text, ports, today)` takes the state and what the user said and
returns the bot's messages and the new state. What the flow reads and writes
goes through *ports* (the ledger, the taxonomy, the model): the service gives
the real ones, the tests a fake. The messages are i18n keys with their values;
the page writes them in the user's language.

Steps:
  ASK_EXPENSE       "Dimmi data e importo della spesa."     (4.1)
  CONFIRM_ONE       one movement shown: "E' questa?"         (4.2 A)
  CHOOSE            several shown: which one?                (4.2 B)
  ASK_CATEGORY      "Qual e' la categoria?"                  (4.3)
  CONFIRM_CATEGORY  one category proposed: "va bene?"        (4.3, 1)
  PICK_NEAREST      the three nearest proposed               (4.3, 2-3)
  PICK_LIST         after three rounds, the whole list       (4.3, 4)
  ALWAYS            "Sempre cosi'?"                          (4.5)
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, NamedTuple, Protocol

from core.spoken_expense import (
    category_match,
    label,
    nearest_categories,
    parse_amount,
    parse_date,
    parse_expense,
    pick_category,
    pick_movement,
    yes_no,
)

ASK_EXPENSE = "ask_expense"
CONFIRM_ONE = "confirm_one"
CHOOSE = "choose"
ASK_CATEGORY = "ask_category"
CONFIRM_CATEGORY = "confirm_category"
PICK_NEAREST = "pick_nearest"
PICK_LIST = "pick_list"
ALWAYS = "always"

# Days before and after the date said: the card date can slip from the
# receipt's (spec 32, 4.2; to verify on data, 7).
WINDOW_DAYS = 3
# Rounds of the category question before the whole list (spec 32, 4.3, 4).
MAX_ROUNDS = 3
_CENT = Decimal("0.01")

Pair = tuple[str, str]


@dataclass(frozen=True)
class Candidate:
    """An outflow that may be the expense said."""
    tx_id: str
    day: date
    amount: Decimal             # positive
    counterpart: str
    category: str | None = None
    subcategory: str | None = None

    @property
    def pair(self) -> Pair | None:
        return (self.category, self.subcategory or "") if self.category else None


class Msg(NamedTuple):
    """One bot message: an i18n key and its values (dates, amounts, pairs as
    they are: the page formats them). *note* is text already written, such as
    what the product wrote when a rule was saved."""
    key: str
    params: dict[str, Any] = {}
    note: str | None = None


class Ports(Protocol):
    def outflows(self, low: Decimal, high: Decimal, date_from: date, date_to: date,
                 checked: bool) -> list[Candidate]: ...
    def category_pairs(self) -> list[Pair]: ...
    def model_pick(self, text: str, labels: list[str]) -> int | None: ...
    def confirm(self, tx_id: str, pair: Pair | None) -> list[dict]: ...
    def always_yes(self, question: dict) -> str | None: ...
    def always_no(self, question: dict) -> None: ...


@dataclass(frozen=True)
class State:
    step: str = ASK_EXPENSE
    day: date | None = None             # said, kept while the amount is missing
    amount: Decimal | None = None       # said, kept while the date is missing
    cents_said: bool = False
    candidates: tuple[Candidate, ...] = ()
    chosen: Candidate | None = None
    proposal: Pair | None = None
    nearest: tuple[Pair, ...] = ()
    refused: tuple[Pair, ...] = ()
    options: list[Pair] = field(default_factory=list)   # the whole list (PICK_LIST)
    words: str = ""                     # the user's last words for the category
    rounds: int = 0
    questions: tuple[dict, ...] = ()


def start() -> tuple[list[Msg], State]:
    return [Msg("expense.ask")], State()


def turn(state: State, text: str, ports: Ports, today: date) -> tuple[list[Msg], State]:
    """One turn of the flow: what the bot says and where the flow goes."""
    handler = {
        ASK_EXPENSE: _expense,
        CONFIRM_ONE: _confirm_one,
        CHOOSE: _choose,
        ASK_CATEGORY: _category_words,
        CONFIRM_CATEGORY: _confirm_category,
        PICK_NEAREST: _pick_nearest,
        PICK_LIST: _pick_list,
        ALWAYS: _always,
    }[state.step]
    return handler(state, text, ports, today)


# ── 4.1 / 4.2 Date, amount, search ───────────────────────────────────────────


def _expense(state, text, ports, today):
    if state.amount is not None and state.day is None:
        # "In che giorno?" "12": a bare number here is the day, not an amount.
        said = parse_amount(text)
        day = parse_date(f"il {text}", today) if said and not said.cents_said else None
        if day is not None and parse_date(text, today) is None:
            return _with_lead([], state.amount, state.cents_said, day, ports)
    p = parse_expense(text, today)
    day = p.day or state.day
    amount, cents = (p.amount, p.cents_said) if p.amount is not None else (state.amount, state.cents_said)
    if day is None and amount is None:
        return [Msg("expense.ask_again")], State()
    if amount is None:
        return [Msg("expense.ask_amount", {"day": day})], State(day=day)
    if day is None:
        return [Msg("expense.ask_date", {"amount": amount})], State(amount=amount, cents_said=cents)
    lead = [] if p.sure else [Msg("expense.understood", {"amount": amount, "day": day})]
    return _with_lead(lead, amount, cents, day, ports)


def _with_lead(lead, amount, cents, day, ports):
    msgs, new = _search(amount, cents, day, ports)
    return lead + msgs, new


def _search(amount: Decimal, cents_said: bool, day: date, ports):
    d_from, d_to = day - timedelta(days=WINDOW_DAYS), day + timedelta(days=WINDOW_DAYS)

    def by_closeness(found):
        return tuple(sorted(found, key=lambda c: (abs((c.day - day).days), c.day, c.tx_id)))

    found = by_closeness(ports.outflows(amount, amount, d_from, d_to, False))
    if len(found) == 1:
        return _show_one(found[0])
    if found:
        return ([Msg("expense.found_many", {"n": len(found), "items": list(found)}), Msg("expense.which")],
                State(step=CHOOSE, candidates=found))
    if not cents_said:
        # "23" may be a 23,40 said in a hurry: the cents of that euro, always
        # as a choice, never confirmed by themselves (spec 32, 4.1).
        near = by_closeness(ports.outflows(amount, amount + 1 - _CENT, d_from, d_to, False))
        if near:
            return ([Msg("expense.found_near", {"amount": amount, "day": day, "items": list(near)}),
                     Msg("expense.which")],
                    State(step=CHOOSE, candidates=near))
    done = by_closeness(ports.outflows(amount, amount, d_from, d_to, True))
    if done:
        c = done[0]
        return ([Msg("expense.already_checked", {"amount": c.amount, "day": c.day, "name": c.counterpart}),
                 Msg("expense.next")], State())
    return _not_found(amount, day)


def _not_found(amount, day):
    # Outcome C. Cash and receipts waiting for the next import are open
    # questions (spec 32, 4.4 and 7): the user tries again.
    return [Msg("expense.not_found", {"amount": amount, "day": day})], State()


def _show_one(c: Candidate, state: State | None = None):
    base = state or State()
    params = {"amount": c.amount, "day": c.day, "name": c.counterpart}
    if c.pair:
        msg = Msg("expense.found_one", {**params, "cat": c.pair})
    else:
        msg = Msg("expense.found_one_nocat", params)
    return [msg], replace(base, step=CONFIRM_ONE, chosen=c)


def _confirm_one(state, text, ports, today):
    c = state.chosen
    answer = yes_no(text)
    if answer == "yes" and c.pair:
        return _finish(state, ports, None)
    if answer in ("yes", "yes_other"):
        return [Msg("expense.ask_category")], replace(state, step=ASK_CATEGORY)
    if answer == "no":
        return _not_found(c.amount, c.day)
    return [Msg("expense.yes_no")], state


def _choose(state, text, ports, today):
    cands = state.candidates
    answer = yes_no(text)
    if answer == "no":
        c = cands[0]
        return _not_found(c.amount, c.day)
    if answer in ("yes", "yes_other") and len(cands) == 1:
        index = 0
    else:
        index = pick_movement(text, [(c.counterpart, c.day) for c in cands], today)
    if index is None:
        return [Msg("expense.pick_again", {"items": list(cands)})], state
    return _show_one(cands[index], state)


# ── 4.3 The category ─────────────────────────────────────────────────────────


def _category_words(state, text, ports, today):
    pairs = ports.category_pairs()
    pair = category_match(text, pairs)
    if pair is None:
        # The model only when the words alone do not name one category.
        labels = [label(p) for p in pairs]
        index = ports.model_pick(text, labels)
        if isinstance(index, int) and 0 <= index < len(pairs):
            pair = pairs[index]
    if pair is not None and pair not in state.refused:
        return ([Msg("expense.propose_category", {"cat": pair})],
                replace(state, step=CONFIRM_CATEGORY, proposal=pair, words=text))
    return _nearest(replace(state, words=text), text, pairs)


def _nearest(state, text, pairs):
    near = tuple(nearest_categories(text, pairs, exclude=list(state.refused)))
    if not near:
        return _whole_list(state, pairs)
    return ([Msg("expense.nearest", {"items": list(near)})],
            replace(state, step=PICK_NEAREST, nearest=near, proposal=None))


def _confirm_category(state, text, ports, today):
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        return _finish(state, ports, state.proposal)
    if answer == "no":
        refused = state.refused + (state.proposal,)
        return _nearest(replace(state, refused=refused), state.words, ports.category_pairs())
    return _new_round(state, text, ports, today)


def _pick_nearest(state, text, ports, today):
    index = pick_category(text, list(state.nearest))
    if index is not None:
        return _finish(state, ports, state.nearest[index])
    if yes_no(text) == "no":
        rounds = state.rounds + 1
        refused = state.refused + state.nearest
        if rounds >= MAX_ROUNDS:
            return _whole_list(replace(state, rounds=rounds, refused=refused), ports.category_pairs())
        return [Msg("expense.other_words")], replace(state, step=ASK_CATEGORY, rounds=rounds,
                                                     refused=refused, nearest=())
    return _new_round(state, text, ports, today)


def _new_round(state, text, ports, today):
    """New words instead of an answer: another round on them."""
    rounds = state.rounds + 1
    if rounds >= MAX_ROUNDS:
        return _whole_list(replace(state, rounds=rounds), ports.category_pairs())
    return _category_words(replace(state, rounds=rounds), text, ports, today)


def _whole_list(state, pairs):
    options = list(pairs)
    return [Msg("expense.pick_list")], replace(state, step=PICK_LIST, options=options)


def _pick_list(state, text, ports, today):
    said = text.strip()
    for pair in state.options:
        if label(pair) == said:
            return _finish(state, ports, pair)
    index = pick_category(text, state.options)
    if index is not None:
        return _finish(state, ports, state.options[index])
    return [Msg("expense.pick_list")], state


# ── 5. The writes, 4.5 "Sempre cosi'?" ───────────────────────────────────────


def _finish(state, ports, pair: Pair | None):
    """The movement checked; with its category by hand when it changed."""
    c = state.chosen
    changed = pair if pair is not None and pair != c.pair else None
    questions = ports.confirm(c.tx_id, changed)
    final = changed or c.pair
    done = Msg("expense.done", {"day": c.day, "amount": c.amount, "name": c.counterpart, "cat": final})
    if changed and questions:
        q = questions[0]
        return ([done, _always_msg(q)],
                State(step=ALWAYS, chosen=c, questions=tuple(questions)))
    return [done, Msg("expense.next")], State()


def _always_msg(q: dict) -> Msg:
    return Msg("ledger.always.ask", {"n": q["n"], "name": q["description"],
                                     "cat": (q["category"], q["subcategory"])})


def _always(state, text, ports, today):
    q = state.questions[0]
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        note = ports.always_yes(q)
        return ([Msg("ledger.always.done", {"name": q["description"],
                                            "cat": (q["category"], q["subcategory"])}, note),
                 Msg("expense.next")], State())
    if answer == "no":
        ports.always_no(q)
        return [Msg("expense.always_kept"), Msg("expense.next")], State()
    return [_always_msg(q)], state
