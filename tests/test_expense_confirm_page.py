"""The expense confirmation page (spec 32, 3.1): chat turns typed or dictated
by the operating system, driven through the real page. Synthetic data."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from db.models import CategoryRule, Transaction, create_tables, get_session
from services.expense_confirm_service import ExpenseConfirmService

YESTERDAY = date.today() - timedelta(days=1)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """The words decide; the model is not there in tests."""
    monkeypatch.setattr(ExpenseConfirmService, "model_pick", lambda self, text, labels: None)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "p.db"
    create_tables(create_engine(f"sqlite:///{path}"))
    return path


def _engine(db):
    return create_engine(f"sqlite:///{db}")


def _add(db, tx_id, amount="-23.40", day=None, desc="ESSELUNGA", category="Alimentari",
         subcategory="Spesa supermercato", source="llm"):
    with get_session(_engine(db)) as s:
        s.add(Transaction(
            id=tx_id, date=(day or YESTERDAY).isoformat(), amount=Decimal(amount), description=desc,
            source_file="f", account_label="Conto", tx_type="expense", category=category,
            subcategory=subcategory, category_source=source))
        s.commit()


def _page(db_path: str) -> None:
    from sqlalchemy import create_engine

    from ui.expense_confirm_page import render_expense_confirm_page
    from ui.i18n import set_language
    set_language("it")
    render_expense_confirm_page(create_engine(f"sqlite:///{db_path}"))


def _open(db):
    app = AppTest.from_function(_page, args=(str(db),), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    return app


def _say(app, text):
    app.chat_input[0].set_value(text).run()
    assert not app.exception, app.exception
    return app


def _bot(app) -> str:
    """The last message of the bot."""
    bots = [m for m in app.chat_message if m.name == "assistant"]
    return "\n".join(md.value for md in bots[-1].markdown)


def test_the_page_opens_with_the_question(db):
    app = _open(db)
    assert "Dimmi data e importo" in _bot(app)


def test_found_and_confirmed(db):
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23 e 40")
    reply = _bot(app)
    assert "Esselunga" in reply and "23,40" in reply and "Alimentari / Spesa supermercato" in reply
    _say(app, "sì")
    assert "Controllata" in _bot(app)
    with get_session(_engine(db)) as s:
        tx = s.get(Transaction, "a")
        assert tx.human_validated
        assert tx.category_source == "llm"


def test_several_found_and_one_chosen(db):
    _add(db, "a")
    _add(db, "b", desc="BAR CENTRALE", day=YESTERDAY - timedelta(days=1))
    app = _open(db)
    _say(app, "ieri, 23,40")
    reply = _bot(app)
    assert "1)" in reply and "Bar Centrale" in reply
    _say(app, "bar")
    assert "Bar Centrale" in _bot(app)
    _say(app, "sì")
    with get_session(_engine(db)) as s:
        assert s.get(Transaction, "b").human_validated
        assert not s.get(Transaction, "a").human_validated


def test_not_found_offers_to_try_again(db):
    app = _open(db)
    _say(app, "ieri, 23,40")
    assert "Non trovo" in _bot(app)


def test_a_category_by_hand_and_always_like_this(db):
    for i in range(2):
        _add(db, f"m{i}", amount="-5", day=YESTERDAY - timedelta(days=10 + i),
             category="Casa", subcategory="Arredamento", source="manual")
    _add(db, "old", amount="-7", day=YESTERDAY - timedelta(days=20))      # the model's
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23,40")
    _say(app, "sì, ma la categoria è un'altra")
    assert "categoria" in _bot(app)
    _say(app, "arredamento")
    assert "Casa / Arredamento" in _bot(app)
    _say(app, "sì")
    assert "Sempre così?" in _bot(app)
    _say(app, "sì")
    with get_session(_engine(db)) as s:
        a = s.get(Transaction, "a")
        assert (a.category, a.subcategory, a.category_source, a.human_validated) == (
            "Casa", "Arredamento", "manual", True)
        old = s.get(Transaction, "old")
        assert (old.category, old.category_source) == ("Casa", "rule")
        assert s.query(CategoryRule).count() == 1


def test_always_no(db):
    for i in range(2):
        _add(db, f"m{i}", amount="-5", day=YESTERDAY - timedelta(days=10 + i),
             category="Casa", subcategory="Arredamento", source="manual")
    _add(db, "a")
    app = _open(db)
    for text in ("ieri, 23,40", "sì ma la categoria è un'altra", "arredamento", "sì", "no"):
        _say(app, text)
    assert "solo per questo movimento" in _bot(app)
    with get_session(_engine(db)) as s:
        assert s.query(CategoryRule).count() == 0


def test_after_three_rounds_the_whole_list(db):
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23,40")
    _say(app, "sì ma la categoria è un'altra")
    for _ in range(3):
        _say(app, "spesa per la casa")
        _say(app, "nessuna")
    assert app.selectbox, "the list of the categories"
    app.selectbox(key="expense_list").set_value("Salute / Farmaci")
    app.button(key="expense_list_pick").click().run()
    assert not app.exception, app.exception
    with get_session(_engine(db)) as s:
        a = s.get(Transaction, "a")
        assert (a.category, a.subcategory, a.human_validated) == ("Salute", "Farmaci", True)


def test_start_again(db):
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23,40")
    app.button(key="expense_restart").click().run()
    assert not app.exception, app.exception
    assert len([m for m in app.chat_message if m.name == "assistant"]) == 1
    assert "Dimmi data e importo" in _bot(app)


def test_a_day_of_another_year_says_the_year():
    from core.expense_confirm import Msg
    from ui.expense_confirm_page import _formatter
    from ui.i18n import set_language
    set_language("it")
    say = _formatter({})
    text = say([Msg("expense.not_found", {"amount": Decimal("1234.5"), "day": date(2001, 5, 12)})])
    assert "12 maggio 2001" in text and "1.234,50" in text


# ── The bot speaks (spec 32, 3.2) ────────────────────────────────────────────

def test_spoken_text_has_no_markdown():
    from ui.expense_confirm_page import spoken_text
    assert spoken_text("Ho trovato **Esselunga**.\n\n- 1) Bar, 12 ottobre\n- 2) Coop") == (
        "Ho trovato Esselunga. 1) Bar, 12 ottobre. 2) Coop")


def test_a_colon_before_a_list_is_not_read_with_a_full_stop():
    from ui.expense_confirm_page import spoken_text
    assert spoken_text("Ne ho trovate 2:\n\n- 1) Bar\n- 2) Coop") == "Ne ho trovate 2: 1) Bar. 2) Coop"


def test_each_reply_has_its_own_voice():
    """The voice of each reply is made for it, from the user's Enter: one
    voice in the app window for every reply came late with the dictation on."""
    from ui.expense_confirm_page import speak_script
    js = speak_script("Ciao", "it-IT")
    assert "window.parent" not in js and "speechSynthesis" in js
    assert '"Ciao"' in js and '"it-IT"' in js


# ── The answer is sent with Enter (spec 32, 3.2) ─────────────────────────────

def test_nothing_sends_the_answer_by_itself():
    import ui.expense_confirm_page as page
    assert not hasattr(page, "newline_sends_script")


def test_amounts_are_read_as_people_say_them(db):
    from decimal import Decimal as D

    from ui.expense_confirm_page import spoken_amount
    from ui.i18n import set_language
    set_language("it")
    assert spoken_amount(D("24.90")) == "24 e 90"
    assert spoken_amount(D("23.05")) == "23 e 05"
    assert spoken_amount(D("0.90")) == "90 centesimi"
    assert spoken_amount(D("24.00")) == "24 euro"


def test_the_reply_reads_the_amount_without_comma(db):
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23 e 40")
    said = app.session_state["_expense_spoken"]
    assert "23 e 40" in said and "23,40" not in said


def test_a_dictation_that_repeats_the_last_answer_gives_only_the_new_part(db):
    """The Mac's dictation writes again what it heard, then the new words:
    the bot gets only these (spec 32, 3.2)."""
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23 e 40")
    _say(app, "ieri 23 e 40 € sì")
    users = [m for m in app.chat_message if m.name == "user"]
    assert users[-1].markdown[0].value == "sì"
    assert "Controllata" in _bot(app)
