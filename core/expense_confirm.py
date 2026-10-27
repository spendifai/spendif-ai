"""The fixed flow that confirms an expense (spec 32, 4): a state machine of
turns, not a free agent (spec 32, 2.1).

`turn(state, text, ports, today)` takes the state and what the user said and
returns the bot's messages and the new state. What the flow reads and writes
goes through *ports* (the ledger, the taxonomy, the model): the service gives
the real ones, the tests a fake. The messages are i18n keys with their values;
the page writes them in the user's language.

Steps:
  ASK_EXPENSE       "Data e importo."                        (4.1)
  CONFIRM_ONE       one movement shown: "Esselunga, ...?"    (4.2 A)
  CHOOSE            several shown: "Quale?"                  (4.2 B)
  ASK_CATEGORY      "Categoria?"                             (4.3)
  CONFIRM_CATEGORY  one category proposed                    (4.3, 1)
  PICK_NEAREST      the three nearest proposed               (4.3, 2-3)
  PICK_LIST         after three rounds, the whole list       (4.3, 4)
  ALWAYS            "Sempre cosi' per Esselunga?"            (4.5)
  CLOSED            the session closed: "Fine. 7 spuntate."  (4.6)
  CHECK             what the model understood, said back: "Chiudo?"  (4.8)
  SAVE_ASK          not there yet: "Lo salvo e lo ritrovo...?"  (4.4bis)
  ASK_CASH          the receipt saved: "In contanti?"            (4.4bis)
  ASK_WHAT          the receipt saved: "Cos'hai comprato?"      (4.4bis)
  ASK_CASH_ACCOUNT  no cash account yet: "Creo il conto Contanti?"  (4.4)
  REVIEW_ASK        receipts with their movement: "Li controlliamo?"  (4.4bis)
  REVIEW_ONE        one movement for a receipt: "Caffe', 2 euro: Bar Centrale?"
  REVIEW_CHOOSE     several movements for a receipt: "quale?"

After "Fatto." the last expense confirmed can have its category corrected
("cambia categoria"): back to ASK_CATEGORY on that movement (4.7).

The bot never repeats what the user just said (spec 32, 2.5): the date of a
movement is said only when it differs from the one said.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, NamedTuple, Protocol

from core.spoken_expense import (
    category_match,
    is_closing,
    is_correction,
    says_an_expense,
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
CLOSED = "closed"
CHECK = "check"
SAVE_ASK = "save_ask"
ASK_CASH = "ask_cash"
ASK_WHAT = "ask_what"
ASK_CASH_ACCOUNT = "ask_cash_account"
REVIEW_ASK = "review_ask"
REVIEW_ONE = "review_one"
REVIEW_CHOOSE = "review_choose"

# What an answer the words do not understand may mean, by the question asked
# (spec 32, 4.8). The model chooses among these only.
INTENTS = {
    "found": ("yes", "yes_other", "no", "close", "unclear"),
    "always": ("yes", "no", "close", "correct", "unclear"),
    "waiting": ("close", "correct", "unclear"),
}
# An intent confirmed, said again in words the flow already understands.
_SAID = {"yes": "si", "yes_other": "si ma categoria", "no": "no"}

# Days before and after the date said: the card date can slip from the
# receipt's (spec 32, 4.2; to verify on data, 7).
WINDOW_DAYS = 3
# The same amount on another day, offered when nothing is in the window: the
# date remembered is wrong more often than the amount (spec 32, 4.2 C 1).
FAR_DAYS = 31
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


@dataclass(frozen=True)
class ReceiptMatch:
    """A receipt saved for the next import (spec 32, 4.4bis) and the
    unchecked outflows that may now be its movement."""
    receipt_id: int
    day: date
    amount: Decimal
    what: str
    pair: Pair | None                   # None: saved without a category, the movement keeps its own
    candidates: tuple[Candidate, ...]


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
    def last_date(self) -> date | None: ...
    def model_pick(self, text: str, labels: list[str]) -> int | None: ...
    def model_intent(self, kind: str, text: str) -> str | None: ...
    def confirm(self, tx_id: str, pair: Pair | None) -> list[dict]: ...
    def always_yes(self, question: dict) -> str | None: ...
    def always_no(self, question: dict) -> None: ...
    def save_receipt(self, day: date, amount: Decimal, what: str, pair: Pair | None, cash: bool) -> None: ...
    def confirm_receipt(self, receipt_id: int, tx_id: str, pair: Pair | None) -> list[dict]: ...
    def cash_account(self) -> str | None: ...
    def create_cash_account(self) -> str: ...
    def cash_movement(self, day: date, amount: Decimal, what: str, pair: Pair | None, account: str) -> None: ...


@dataclass(frozen=True)
class State:
    step: str = ASK_EXPENSE
    day: date | None = None             # said, kept while the amount is missing
    amount: Decimal | None = None       # said, kept while the date is missing
    cents_said: bool = False
    candidates: tuple[Candidate, ...] = ()
    chosen: Candidate | None = None
    proposal: Pair | None = None
    saved_pair: Pair | None = None      # ASK_CASH_ACCOUNT: the category chosen for the cash receipt
    nearest: tuple[Pair, ...] = ()
    refused: tuple[Pair, ...] = ()
    options: list[Pair] = field(default_factory=list)   # the whole list (PICK_LIST)
    words: str = ""                     # the user's last words for the category
    rounds: int = 0
    questions: tuple[dict, ...] = ()
    said_day: date | None = None        # the date said, to name a movement's only when different
    checked: int = 0                    # movements checked in this session (spec 32, 4.6)
    last: Candidate | None = None       # the last one checked, with its category now (spec 32, 4.7)
    pending: str | None = None          # CHECK: the intent the model understood
    before: State | None = None         # CHECK: where the flow was
    receipt: bool = False               # the category is for a receipt to save (4.4bis), day and amount above
    what: str = ""                      # the receipt's description, said by the user
    cash: bool = False                  # the receipt was paid in cash: never matched to a statement
    reviews: tuple[ReceiptMatch, ...] = ()   # receipts with movements, to check one at a time
    claimed: tuple[str, ...] = ()       # movements already given to a receipt in this review


def start(reviews: list[ReceiptMatch] | tuple = ()) -> tuple[list[Msg], State]:
    """The first turn: the receipts saved that now have a movement, if any
    (spec 32, 4.4bis), else the usual question."""
    if reviews:
        key = "expense.receipts_found_one" if len(reviews) == 1 else "expense.receipts_found"
        return [Msg(key, {"n": len(reviews)})], \
            State(step=REVIEW_ASK, reviews=tuple(reviews))
    return [Msg("expense.ask")], State()


def turn(state: State, text: str, ports: Ports, today: date) -> tuple[list[Msg], State]:
    """One turn of the flow: what the bot says and where the flow goes."""
    if state.step == CLOSED:
        return [], state
    if state.step == CHECK:
        answer = yes_no(text)
        if answer == "yes":             # "si', ma..." is not a plain yes: it answers before
            return _apply(state, ports, today)
        if answer == "no":
            return _ask_again(state.before), state.before
        state = state.before            # other words: they answer the question of before
    waiting = state.step == ASK_EXPENSE and state.amount is None and state.day is None
    if is_closing(text, waiting):
        return _close(state)
    if (waiting or state.step == ALWAYS) and state.last is not None and is_correction(text):
        return _correct(state)
    checking = _model_check(state, text, waiting, ports, today)
    if checking is not None:
        return checking
    handler = {
        ASK_EXPENSE: _expense,
        CONFIRM_ONE: _confirm_one,
        CHOOSE: _choose,
        ASK_CATEGORY: _category_words,
        CONFIRM_CATEGORY: _confirm_category,
        PICK_NEAREST: _pick_nearest,
        PICK_LIST: _pick_list,
        ALWAYS: _always,
        SAVE_ASK: _save_ask,
        ASK_CASH: _ask_cash,
        ASK_WHAT: _ask_what,
        ASK_CASH_ACCOUNT: _ask_cash_account,
        REVIEW_ASK: _review_ask,
        REVIEW_ONE: _review_one,
        REVIEW_CHOOSE: _review_choose,
    }[state.step]
    msgs, new = handler(state, text, ports, today)
    # The count and the last one checked go on across expenses; only
    # _finish changes them.
    if new.checked < state.checked:
        new = replace(new, checked=state.checked)
    if new.last is None and state.last is not None:
        new = replace(new, last=state.last)
    return msgs, new


def _model_check(state, text, waiting, ports, today):
    """An answer the words do not understand goes to the model, which only
    proposes: the bot says back what it understood and waits for a yes or a
    no (spec 32, 4.8). None when the words understand it, or the model has
    nothing to say."""
    if state.step == CONFIRM_ONE and yes_no(text) is None:
        kind = "found"
    elif state.step == ALWAYS and yes_no(text) is None:
        kind = "always"
    elif waiting and not says_an_expense(text):
        kind = "waiting"
    else:
        return None
    intent = ports.model_intent(kind, text)
    if intent not in INTENTS[kind] or intent == "unclear":
        return None
    if intent == "correct" and state.last is None:
        return None
    # At "Sempre cosi'?" a no keeps the category for this movement only.
    key = "expense.check.no_rule" if kind == "always" and intent == "no" else f"expense.check.{intent}"
    return ([Msg(key)],
            State(step=CHECK, pending=intent, before=state, checked=state.checked, last=state.last))


def _apply(state, ports, today):
    """The intent the model understood, confirmed by the user."""
    before, intent = state.before, state.pending
    if intent == "close":
        return _close(before)
    if intent == "correct":
        return _correct(before)
    return turn(before, _SAID[intent], ports, today)


def _ask_again(state):
    """The question of before, after a no to what the model understood."""
    if state.step == CONFIRM_ONE:
        return _show_one(state.chosen, replace(state, said_day=None))[0]
    if state.step == ALWAYS:
        return [_always_msg(state.questions[0])]
    return [Msg("expense.ask")]


def _correct(state):
    """The category of the last expense confirmed, asked again (spec 32,
    4.7): the same steps as 4.3 on that movement, which stays checked. At
    "Sempre cosi'?" the question stays unanswered."""
    c = state.last
    return ([Msg("expense.correct", {"name": c.counterpart})],
            State(step=ASK_CATEGORY, chosen=c, checked=state.checked, last=c))


def _close(state):
    """The session closed (spec 32, 4.6): an expense halfway is not written,
    and is said; at "Sempre cosi'?" the movement is already checked and the
    question stays unanswered."""
    msgs = []
    amount = state.chosen.amount if state.chosen else (
        state.candidates[0].amount if state.candidates else state.amount)
    if state.step != ALWAYS and amount is not None:
        msgs.append(Msg("expense.left", {"amount": amount}))
    n = state.checked
    key = "expense.closed_none" if n == 0 else "expense.closed_one" if n == 1 else "expense.closed"
    msgs.append(Msg(key, {"n": n}))
    return msgs, State(step=CLOSED, checked=n)


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
    return lead + msgs, replace(new, said_day=day) if new.step in (CONFIRM_ONE, CHOOSE) else new


def _search(amount: Decimal, cents_said: bool, day: date, ports):
    d_from, d_to = day - timedelta(days=WINDOW_DAYS), day + timedelta(days=WINDOW_DAYS)

    def by_closeness(found):
        return tuple(sorted(found, key=lambda c: (abs((c.day - day).days), c.day, c.tx_id)))

    found = by_closeness(ports.outflows(amount, amount, d_from, d_to, False))
    if len(found) == 1:
        return _show_one(found[0], State(said_day=day))
    if found:
        return ([Msg("expense.found_many", {"n": len(found), "items": list(found)})],
                State(step=CHOOSE, candidates=found))
    if not cents_said:
        # "23" may be a 23,40 said in a hurry: the cents of that euro, always
        # as a choice, never confirmed by themselves (spec 32, 4.1).
        near = by_closeness(ports.outflows(amount, amount + 1 - _CENT, d_from, d_to, False))
        if near:
            return ([Msg("expense.found_near", {"amount": amount, "day": day, "items": list(near)})],
                    State(step=CHOOSE, candidates=near))
    done = by_closeness(ports.outflows(amount, amount, d_from, d_to, True))
    if done:
        return _already_checked(done[0])
    # Outcome C says why (spec 32, 4.2 C): the same amount on another day,
    # then a date the statements do not reach yet, then not there.
    f_from, f_to = day - timedelta(days=FAR_DAYS), day + timedelta(days=FAR_DAYS)
    far = by_closeness(ports.outflows(amount, amount, f_from, f_to, False))
    if len(far) == 1:
        return _show_one(far[0], State(said_day=day))
    if far:
        # Each line carries its own date: no lead needed.
        return ([Msg("expense.found_many", {"n": len(far), "items": list(far)})],
                State(step=CHOOSE, candidates=far))
    far_done = by_closeness(ports.outflows(amount, amount, f_from, f_to, True))
    if far_done:
        return _already_checked(far_done[0])
    last = ports.last_date()
    if last is not None and day > last:
        return [Msg("expense.not_yet", {"last": last}), Msg("expense.save_ask")], \
            State(step=SAVE_ASK, day=day, amount=amount)
    return _not_found(amount, day)


def _already_checked(c: Candidate):
    return [Msg("expense.already_checked", {"amount": c.amount, "day": c.day, "name": c.counterpart})], State()


def _not_found(amount, day):
    # Outcome C: the receipt can wait for the next import (spec 32, 4.4bis).
    return ([Msg("expense.not_found", {"amount": amount, "day": day}), Msg("expense.save_ask")],
            State(step=SAVE_ASK, day=day, amount=amount))


def _save_ask(state, text, ports, today):
    """"Lo salvo?": a yes goes on to its category, a no starts again, a new
    expense said instead is searched."""
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        return [Msg("expense.ask_cash")], replace(state, step=ASK_CASH)
    if answer == "no":
        return [Msg("expense.ask")], State()
    if says_an_expense(text):
        return _expense(State(), text, ports, today)
    return [Msg("expense.yes_no")], state


def _show_one(c: Candidate, state: State | None = None):
    base = state or State()
    params = {"amount": c.amount, "day": c.day, "name": c.counterpart}
    if c.pair:
        msg = Msg("expense.found_one", {**params, "cat": c.pair})
    else:
        msg = Msg("expense.found_one_nocat", params)
    # The date said is not repeated; a different one is the news.
    lead = [Msg("expense.on_day", {"day": c.day})] if base.said_day and c.day != base.said_day else []
    return lead + [msg], replace(base, step=CONFIRM_ONE, chosen=c)


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
    # Chosen from lines that show their date: no date to add.
    return _show_one(cands[index], replace(state, said_day=None))


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
    if state.receipt:
        return _save_receipt(state, ports, pair)
    c = state.chosen
    changed = pair if pair is not None and pair != c.pair else None
    questions = ports.confirm(c.tx_id, changed)
    final = changed or c.pair
    done = Msg("expense.done", {"day": c.day, "amount": c.amount, "name": c.counterpart, "cat": final})
    # A correction checks the same movement again: it is not one more.
    checked = state.checked if state.last is not None and state.last.tx_id == c.tx_id else state.checked + 1
    last = replace(c, category=final[0], subcategory=final[1]) if final else c
    if changed and questions:
        q = questions[0]
        return ([done, _always_msg(q)],
                State(step=ALWAYS, chosen=c, questions=tuple(questions), checked=checked, last=last))
    return [done], State(checked=checked, last=last)


def _ask_cash(state, text, ports, today):
    """"In contanti?": cash never reaches a statement, a card does; a week
    later nobody remembers which (founder, 2026-10-27)."""
    answer = yes_no(text)
    if answer is None:
        return [Msg("expense.yes_no")], state
    return [Msg("expense.ask_what")], replace(state, step=ASK_WHAT, cash=answer != "no")


def _ask_what(state, text, ports, today):
    """"Cos'hai comprato?": the user's own words, which tell two equal
    expenses apart later, and which already propose the category."""
    what = text.strip()
    if not what:
        return [Msg("expense.ask_what")], state
    base = replace(state, step=ASK_CATEGORY, receipt=True, what=what)
    msgs, new = _category_words(base, what, ports, today)
    if new.step == CONFIRM_CATEGORY:
        return msgs, new
    return [Msg("expense.ask_category")], base          # the words do not name one: asked plainly


def _save_receipt(state, ports, pair: Pair):
    """The receipt kept with its date, amount, description and category
    until the user checks it against its movement (spec 32, 4.4bis). In
    cash it is a movement of the cash account at once (4.4), and when there
    is no such account the bot offers to create it."""
    if state.cash:
        account = ports.cash_account()
        if account:
            return _cash_movement(state, ports, pair, account)
        return [Msg("expense.ask_cash_account")], replace(state, step=ASK_CASH_ACCOUNT, saved_pair=pair)
    return _keep_receipt(state, ports, pair)


def _cash_movement(state, ports, pair, account: str):
    ports.cash_movement(state.day, state.amount, state.what, pair, account)
    return ([Msg("expense.cash_recorded", {"what": state.what, "day": state.day, "amount": state.amount,
                                           "cat": pair, "account": account})],
            State(checked=state.checked + 1, last=state.last))


def _ask_cash_account(state, text, ports, today):
    """"Creo il conto Contanti?": yes creates it and the receipt enters it;
    no keeps the receipt waiting, marked as cash."""
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        return _cash_movement(state, ports, state.saved_pair, ports.create_cash_account())
    if answer == "no":
        return _keep_receipt(state, ports, state.saved_pair)
    return [Msg("expense.yes_no")], state


def _keep_receipt(state, ports, pair: Pair):
    ports.save_receipt(state.day, state.amount, state.what, pair, state.cash)
    key = "expense.receipt_saved_cash" if state.cash else "expense.receipt_saved"
    return ([Msg(key, {"what": state.what, "day": state.day, "amount": state.amount, "cat": pair})],
            State(checked=state.checked, last=state.last))


# ── 4.4bis The receipts saved, checked at the start ──────────────────────────


def _review_ask(state, text, ports, today):
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        return _next_review(state)
    if answer == "no":
        return [Msg("expense.ask")], State(checked=state.checked, last=state.last)
    return [Msg("expense.yes_no")], state


def _next_review(state):
    """The next receipt with a movement not given to another one; at the
    end, the usual question."""
    reviews = state.reviews
    while reviews:
        r = reviews[0]
        free = tuple(c for c in r.candidates if c.tx_id not in state.claimed)
        if free:
            new = replace(state, reviews=reviews, candidates=free)
            params = {"what": r.what, "amount": r.amount, "day": r.day}
            if len(free) == 1:
                return ([Msg("expense.review_one", {**params, "name": free[0].counterpart})],
                        replace(new, step=REVIEW_ONE))
            return ([Msg("expense.review_many", {**params, "items": list(free)})],
                    replace(new, step=REVIEW_CHOOSE))
        reviews = reviews[1:]
    return [Msg("expense.ask")], State(checked=state.checked, last=state.last)


def _review_one(state, text, ports, today):
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        return _take_review(state, ports, state.candidates[0])
    if answer == "no":
        return _next_review(replace(state, reviews=state.reviews[1:]))
    return [Msg("expense.yes_no")], state


def _review_choose(state, text, ports, today):
    if yes_no(text) == "no":
        return _next_review(replace(state, reviews=state.reviews[1:]))
    cands = state.candidates
    index = pick_movement(text, [(c.counterpart, c.day) for c in cands], today)
    if index is None:
        return [Msg("expense.pick_again", {"items": list(cands)})], state
    return _take_review(state, ports, cands[index])


def _take_review(state, ports, c: Candidate):
    """The receipt's category, not discussed; when it changes the
    movement's, a change by hand like any other: "Sempre cosi'?" (4.5),
    then the next receipt."""
    r = state.reviews[0]
    questions = ports.confirm_receipt(r.receipt_id, c.tx_id, r.pair if r.pair and r.pair != c.pair else None)
    final = r.pair or c.pair
    last = replace(c, category=final[0], subcategory=final[1]) if final else c
    done = Msg("expense.done", {"day": c.day, "amount": c.amount, "name": c.counterpart, "cat": final})
    rest = replace(state, reviews=state.reviews[1:], claimed=state.claimed + (c.tx_id,),
                   checked=state.checked + 1, last=last)
    if questions:
        return ([done, _always_msg(questions[0])],
                replace(rest, step=ALWAYS, chosen=c, questions=tuple(questions)))
    msgs, new = _next_review(rest)
    return [done] + msgs, replace(new, checked=rest.checked, last=last)


def _always_msg(q: dict) -> Msg:
    return Msg("expense.always", {"n": q["n"], "name": q["description"],
                                  "cat": (q["category"], q["subcategory"])})


def _always(state, text, ports, today):
    q = state.questions[0]
    answer = yes_no(text)
    if answer in ("yes", "yes_other"):
        note = ports.always_yes(q)
        return _after_always(state, [Msg("expense.rule_done", {"name": q["description"],
                                                               "cat": (q["category"], q["subcategory"])}, note)])
    if answer == "no":
        ports.always_no(q)
        return _after_always(state, [Msg("expense.always_kept")])
    return [_always_msg(q)], state


def _after_always(state, msgs):
    """After "Sempre cosi'?": the next receipt to check, if the question
    came from one (4.4bis), else a new expense."""
    if not state.reviews:
        return msgs, State()
    more, new = _next_review(replace(state, step=REVIEW_ONE, questions=(), chosen=None))
    return msgs + more, new
