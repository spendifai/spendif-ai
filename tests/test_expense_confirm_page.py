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
    assert _bot(app) == "Data e importo."


def test_found_and_confirmed(db):
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23 e 40")
    reply = _bot(app)
    # what the user just said is not repeated (spec 32, 2.5)
    assert "Esselunga" in reply and "Alimentari / Spesa supermercato" in reply and "23,40" not in reply
    _say(app, "sì")
    assert _bot(app) == "Fatto."
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


def test_not_found_offers_to_save_the_receipt(db):
    app = _open(db)
    _say(app, "ieri, 23,40")
    assert _bot(app) == "Non c'è.\n\nLo salvo e lo ritrovo quando importi l'estratto conto?"


def test_a_saved_receipt_shows_as_waiting_and_can_be_forgotten(db):
    app = _open(db)
    _say(app, "ieri, 23,40")
    _say(app, "sì")
    assert _bot(app) == "Cos'hai comprato?"
    _say(app, "spesa supermercato")
    _say(app, "sì")
    assert _bot(app).startswith("Salvato: spesa supermercato, 23,40")
    assert any(e.label == "🧾 1 scontrino in attesa dell'estratto conto" for e in app.expander)
    app.button(key="expense_receipt_forget_1").click().run()
    assert not app.exception, app.exception
    assert not any("in attesa" in e.label for e in app.expander)
    assert ExpenseConfirmService(_engine(db)).pending_receipts() == []


def test_a_category_by_hand_and_always_like_this(db):
    for i in range(2):
        _add(db, f"m{i}", amount="-5", day=YESTERDAY - timedelta(days=10 + i),
             category="Casa", subcategory="Arredamento", source="manual")
    _add(db, "old", amount="-7", day=YESTERDAY - timedelta(days=20))      # the model's
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23,40")
    _say(app, "sì, ma la categoria è un'altra")
    assert _bot(app) == "Categoria?"
    _say(app, "arredamento")
    assert "Casa / Arredamento" in _bot(app)
    _say(app, "sì")
    assert "Sempre così per **Esselunga**?" in _bot(app)
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
    assert "Solo questa." in _bot(app)
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
    assert _bot(app) == "Data e importo."


def test_a_day_of_another_year_says_the_year():
    from core.expense_confirm import Msg
    from ui.expense_confirm_page import _formatter
    from ui.i18n import set_language
    set_language("it")
    say = _formatter({})
    text = say([Msg("expense.understood", {"amount": Decimal("1234.5"), "day": date(2001, 5, 12)})])
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
    _add(db, "b", desc="BAR CENTRALE")       # two found: the list says the amounts
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
    assert _bot(app) == "Fatto."



# ── Closing (spec 32, 4.6) ───────────────────────────────────────────────────


def test_closing_counts_and_stops_listening(db):
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23,40")
    _say(app, "sì")
    _say(app, "ho finito")
    assert _bot(app) == "Fine. 1 spuntata."
    assert app.chat_input[0].disabled
    _ = app.button(key="expense_restart").click().run()
    assert not app.exception, app.exception
    assert _bot(app) == "Data e importo." and not app.chat_input[0].disabled


def test_a_rule_note_is_shown_and_not_read_aloud():
    from core.expense_confirm import Msg
    from ui.expense_confirm_page import _formatter
    from ui.i18n import set_language
    set_language("it")
    say = _formatter({})
    msgs = [Msg("expense.rule_done", {}, "Applicata a 4 movimenti.")]
    assert "Applicata a 4 movimenti." in say(msgs)
    assert say(msgs, spoken=True) == "Regola creata."


def _fixed_boxes(node):
    """The blocks of fixed height in the page, at any depth."""
    out = []
    for child in getattr(node, "children", {}).values():
        hc = getattr(child.proto, "height_config", None)
        if hc is not None and hc.WhichOneof("height_spec") == "pixel_height":
            out.append(child)
        out += _fixed_boxes(child)
    return out


def test_the_conversation_scrolls_inside_a_box_of_fixed_height(db):
    """The page does not grow with the chat: the messages live in a box of
    fixed height that scrolls by itself to the last one."""
    _add(db, "a")
    app = _open(db)
    _say(app, "ieri, 23,40")
    boxes = [b for b in _fixed_boxes(app.main) if len(b.chat_message)]
    assert len(boxes) == 1 and boxes[0].proto.height_config.pixel_height == 420
    assert len(boxes[0].chat_message) == len(app.chat_message) == 3


# ── The listening model, offered on first use (spec 32, 3) ───────────────────


@pytest.fixture
def _offered(monkeypatch):
    """The library is in the package and the model is not there yet; the
    download only records that it was asked for."""
    from services.voice_service import VoiceService
    started: list[bool] = []
    monkeypatch.setattr(VoiceService, "can_offer_listening", lambda self, language: True)
    monkeypatch.setattr(VoiceService, "start_listening_download", lambda self: started.append(True))
    return started


def test_the_listening_model_is_offered_and_downloaded_only_after_yes(db, _offered):
    app = _open(db)
    assert any(b.key == "expense_ears_yes" for b in app.button) and not _offered
    app.button(key="expense_ears_yes").click().run()
    assert not app.exception, app.exception
    assert _offered == [True]


def test_after_a_no_a_small_button_asks_again(db, _offered):
    from services.settings_service import SettingsService
    app = _open(db)
    app.button(key="expense_ears_no").click().run()
    assert not app.exception, app.exception
    assert SettingsService(_engine(db)).get("voice_listen_declined") == "true"
    keys = [b.key for b in app.button]
    assert "expense_ears_yes" not in keys and "expense_ears_again" in keys
    app.button(key="expense_ears_again").click().run()
    assert any(b.key == "expense_ears_yes" for b in app.button) and not _offered


def test_no_question_where_the_ears_cannot_be_offered(db, monkeypatch):
    from services.voice_service import VoiceService
    monkeypatch.setattr(VoiceService, "can_offer_listening", lambda self, language: False)
    app = _open(db)
    assert not any(b.key in ("expense_ears_yes", "expense_ears_again") for b in app.button)


def test_the_page_opens_on_the_receipts_with_a_movement(db):
    ExpenseConfirmService(_engine(db)).save_receipt(YESTERDAY, Decimal("2"), "caffè", ("Casa", "Arredamento"))
    _add(db, "bar", amount="-2", desc="BAR CENTRALE", category=None, subcategory=None)
    app = _open(db)
    assert _bot(app) == "Ho trovato il movimento di uno scontrino già salvato. Lo controlliamo?"
    _say(app, "sì")
    assert _bot(app).startswith("caffè, 2,00")
    _say(app, "sì")
    assert ExpenseConfirmService(_engine(db)).pending_receipts() == []
