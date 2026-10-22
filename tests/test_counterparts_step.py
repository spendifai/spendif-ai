"""The counterparts step of the guided workflow.

Progress is counted in money over the spending of the landing period; the
tick "always like this" turns the category shown into a rule for every
movement of the counterpart. The step is about how the money was spent: a
counterpart one of the user's rules covers is done, at every import, and a
movement checked as spent in the ledger does not count here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from core.guided_flow import check_progress
from db.models import Transaction, create_tables, get_session
from db.repository import create_category_rule, get_category_rules, get_counterpart_stats
from services.guided_flow_service import GuidedFlowService
from services.settings_service import SettingsService
from ui.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """Rules are written without a model here: the merchant list or the exact text."""
    from services.rule_service import RuleService

    def _down(*a, **kw):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(RuleService, "_ask_model_for_keys", _down)


@pytest.fixture(autouse=True)
def _keep_language():
    """The page runs in English here: leave the language as it was found."""
    before = get_language()
    yield
    set_language(before)


def _tx(session, tx_id, description, amount, day, *, tx_type="expense",
        category="Alimentari", source="llm", validated=False):
    from datetime import UTC, datetime
    session.add(Transaction(
        id=tx_id, date=day, amount=Decimal(str(amount)), currency="EUR",
        description=description, source_file="f.csv", doc_type="bank_statement",
        account_label="BancaX", tx_type=tx_type, to_review=False,
        category=category, subcategory="", category_source=source,
        human_validated=validated,
        validated_at=datetime.now(UTC) if validated else None,
    ))


# ── Progress, pure ───────────────────────────────────────────────────────────

def test_progress_on_no_spending_is_reached():
    p = check_progress([])
    assert (p.total, p.missing, p.needed, p.reached) == (0, 0, 0, True)


def test_progress_counts_the_largest_unchecked_first():
    p = check_progress([(500, False), (300, False), (150, True), (50, False)])
    assert p.total == 1000
    assert p.checked == 150
    assert p.missing == pytest.approx(650)
    assert p.needed == 2            # 150 + 500 + 300 reaches 800
    assert not p.reached


def test_progress_reached_at_the_goal():
    p = check_progress([(800, True), (200, False)])
    assert p.reached
    assert (p.missing, p.needed) == (0, 0)


# ── Period figures ───────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    return eng


def test_a_rule_makes_the_counterpart_categorized_for_every_import(engine):
    """A rule decided once covers the movements of later imports, whatever
    source they carry: the counterpart is never asked again."""
    with get_session(engine) as s:
        create_category_rule(s, pattern="Coop", match_type="exact",
                             category="Alimentari", subcategory="", priority=10)
        _tx(s, "a", "Coop", -30, "2026-08-02")
        _tx(s, "b", "Coop", -50, "2026-08-20")
        _tx(s, "c", "Bar", -5, "2026-08-11")
        _tx(s, "d", "Bar", -5, "2026-01-11")                      # outside the period
        s.commit()
        stats = get_counterpart_stats(s, date_from="2026-06-01", date_to="2026-08-31")
    assert [(x["description"], x["tx_count"], x["total_amount"], x["categorized_by_user"])
            for x in sorted(stats, key=lambda x: x["description"])] == [
        ("Bar", 1, 5.0, False), ("Coop", 2, 80.0, True),
    ]


def test_categorized_comes_from_the_category_source_not_from_the_check(engine):
    """Checking a movement in the ledger says the money was spent, not how:
    it does not categorize the counterpart. A category set by hand does."""
    with get_session(engine) as s:
        _tx(s, "a", "Coop", -30, "2026-08-02", validated=True)
        _tx(s, "b", "Bar", -5, "2026-08-11", source="manual")
        s.commit()
        stats = {x["description"]: x for x in get_counterpart_stats(
            s, date_from="2026-06-01", date_to="2026-08-31")}
    assert stats["Coop"]["categorized_by_user"] is False
    assert stats["Coop"]["uncategorized_count"] == 1
    assert stats["Bar"]["categorized_by_user"] is True
    assert stats["Bar"]["uncategorized_count"] == 0


def test_reimport_under_an_existing_rule_does_not_ask_again(engine):
    """The case that made the user redo the work: the ledger is emptied, the
    rules stay, the files are imported again. The counterparts the rules
    cover come back already done, and only the others are listed."""
    SettingsService(engine).create_account("BancaX", "")
    with get_session(engine) as s:
        create_category_rule(s, pattern="Affitto", match_type="exact",
                             category="Casa", subcategory="", priority=10)
        for i in range(4):
            _tx(s, f"a{i}", "Affitto", -900, f"2026-0{6 + i % 3}-{10 + i}", category="Casa", source="rule")
        # 400 out of 4000: over 5% of the spending, so asked (not a small single).
        _tx(s, "b", "Coop", -400, "2026-08-03")
        s.commit()
    svc = GuidedFlowService(engine)
    assert [x["description"] for x in svc.expense_counterparts()
            if not x["categorized_by_user"]] == ["Coop"]
    assert svc.check_progress().checked == 4          # movements, 4 out of 5
    assert "counterparts" in svc.closed_steps()


def test_step_lists_the_counterparts_with_most_movements_first(engine):
    """What repeats first: decided once for every import, and the one that
    comes written in different ways (founder, 2026-10-20). Progress counts
    spending movements, in the same order, so "the first N" is true."""
    with get_session(engine) as s:
        _tx(s, "a", "Affitto", -900, "2026-08-01", category="Casa")
        _tx(s, "b", "Coop", -60, "2026-08-03")
        _tx(s, "c", "Coop", -40, "2026-07-03")
        _tx(s, "d", "Stipendio", 2500, "2026-08-27", tx_type="income", category="Reddito")
        _tx(s, "e", "Vecchio", -10, "2026-01-03")
        s.commit()
    svc = GuidedFlowService(engine)
    assert [x["description"] for x in svc.expense_counterparts()] == ["Coop", "Affitto"]
    p = svc.check_progress()
    assert (p.total, p.checked, p.needed) == (3, 0, 2)


def test_same_movements_the_largest_total_first(engine):
    with get_session(engine) as s:
        _tx(s, "a", "Bar", -15, "2026-08-01")
        _tx(s, "b", "Treno", -40, "2026-08-02")
        _tx(s, "c", "Libri", -25, "2026-08-03")
        s.commit()
    assert [x["description"] for x in GuidedFlowService(engine).expense_counterparts()] == [
        "Treno", "Libri", "Bar"]


def test_single_counterparts_up_to_the_yearly_threshold_are_not_asked(engine):
    """Founder, 2026-10-21: the threshold comes from the last five years: in a
    typical year the single counterparts up to it stay under 5% of the
    spending. Here all in 2026, spending 1000, 5% is 50: 10 + 20 fit, 30 not."""
    with get_session(engine) as s:
        _tx(s, "a", "Affitto", -900, "2026-08-01")
        _tx(s, "b", "Edicola", -10, "2026-08-02")
        _tx(s, "c", "Farmacia", -20, "2026-08-03")
        _tx(s, "d", "Libri", -30, "2026-08-04")
        _tx(s, "e", "Caffe", -20, "2026-08-05")                  # repeated: always asked
        _tx(s, "f", "Caffe", -20, "2026-08-06")
        s.commit()
    svc = GuidedFlowService(engine)
    assert svc.small_single_limit(date(2026, 10, 21)) == 20
    small = svc.small_singles()
    assert (sorted(small.descriptions), small.total, small.limit) == (["Edicola", "Farmacia"], 30, 20)
    assert [x["description"] for x in svc.expense_counterparts()] == ["Caffe", "Affitto", "Libri"]
    assert svc.check_progress().total == 4
    # On request they show; the goal does not change.
    assert len(svc.expense_counterparts(include_small=True)) == 5
    assert svc.check_progress().total == 4


def test_the_threshold_is_per_year_and_never_splits_equal_amounts():
    from core.guided_flow import small_single_limit
    # Two years of 1000: 5% of 2000 is 100. BAR is single each year (2 x 30),
    # then BOOKS (40) would reach 100: under 5% means strictly, so it stays out.
    movements = [("2025", "rent", 900), ("2025", "bar", 30), ("2025", "x", 70),
                 ("2026", "rent", 900), ("2026", "bar", 30), ("2026", "books", 40),
                 ("2026", "x", 15), ("2026", "x", 15)]
    assert small_single_limit(movements) == 30
    # Equal amounts stay together: 2 + 2 fit under 5, a third 2 would not.
    eq = [("2026", k, 2) for k in "abc"] + [("2026", "big", 94), ("2026", "big", 0.0)]
    assert small_single_limit(eq) == 0
    assert small_single_limit([]) == 0


def test_step_closes_when_the_goal_is_reached(engine):
    SettingsService(engine).create_account("BancaX", "")
    with get_session(engine) as s:
        for i in range(4):
            _tx(s, f"a{i}", "AFFITTO", -900, f"2026-0{6 + i % 3}-{10 + i}", source="manual")
        _tx(s, "b", "COOP", -100, "2026-08-03")
        s.commit()
    assert "counterparts" in GuidedFlowService(engine).closed_steps()   # 4 movements out of 5


# ── The page inside the workflow ─────────────────────────────────────────────

def _guided_script(db_path: str) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    import streamlit as st
    from sqlalchemy import create_engine

    from ui.counterparts_page import render_counterparts_page
    from ui.i18n import set_language

    set_language("en")
    st.session_state["page"] = "guided"
    render_counterparts_page(create_engine(f"sqlite:///{db_path}"))


@pytest.fixture
def ledger(tmp_path):
    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        _tx(s, "a1", "AFFITTO", -450, "2026-08-01", category="Casa")
        _tx(s, "a2", "AFFITTO", -450, "2026-07-01", category="Casa")
        _tx(s, "b", "COOP", -100, "2026-08-03", source="manual")
        s.commit()
    return db_path, eng


def _run(db_path):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_guided_script, kwargs={"db_path": str(db_path)})
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


def test_guided_view_explains_the_criterion_and_what_is_missing(ledger):
    app = _run(ledger[0])
    text = " ".join(m.value for m in app.markdown)
    assert "Once per counterpart is enough" in text
    assert "8 spending movements out of 10" in text
    info = " ".join(i.value for i in app.info)
    assert "1 spending movements out of 3" in info        # categorized, total
    assert "2 are missing" in info                         # to 8 out of 10 of 3
    assert "the first counterpart in the list" in info
    assert any("move on whenever you want" in c.value for c in app.caption)
    assert list(app.dataframe[0].value.iloc[:, 0]) == ["Affitto"]   # categorized ones hidden


def test_ticking_always_makes_the_category_a_rule_for_every_movement(ledger):
    from ui.i18n import t

    db_path, eng = ledger
    set_language("en")
    app = _run(db_path)
    edit = {"edited_rows": {0: {t("counterparts.col_categorized"): True}},
            "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    save = [b for b in app.button if b.key == "cp_save"]
    assert save, "the tick was not seen as a change"
    app.session_state["cp_editor"] = edit
    save[0].click().run(timeout=30)
    assert not app.exception, app.exception

    with get_session(eng) as s:
        rules = [(r.pattern, r.match_type, r.category) for r in get_category_rules(s)]
        assert rules == [("Affitto", "exact", "Casa")]
        for tx_id in ("a1", "a2"):
            tx = s.get(Transaction, tx_id)
            assert tx.category == "Casa"
            assert tx.validated_at is None          # categorized, not checked
            assert tx.category_source == "rule"
    assert GuidedFlowService(eng).check_progress().reached


def test_a_tick_on_a_known_merchant_writes_a_starts_with_rule(tmp_path):
    """Spec 18: the product writes the rule. The merchant list knows TAMOIL in
    Italy, so the tick gives "starts with TAMOIL" and the other station of the
    same brand is categorized too, with a note saying how the rule was written."""
    from ui.i18n import t

    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    SettingsService(eng).set("country", "It")
    with get_session(eng) as s:
        _tx(s, "t1", "Tamoil Gaggiano", -60, "2026-08-01", category="Trasporti")
        _tx(s, "t2", "Tamoil Rozzano", -40, "2026-08-10", category="Altro")
        s.commit()
    set_language("en")
    app = _run(db_path)
    rows = list(app.dataframe[0].value.iloc[:, 0])
    edit = {"edited_rows": {rows.index("Tamoil Gaggiano"): {t("counterparts.col_categorized"): True}},
            "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    app.session_state["cp_editor"] = edit
    [b for b in app.button if b.key == "cp_save"][0].click().run(timeout=30)
    assert not app.exception, app.exception

    with get_session(eng) as s:
        assert [(r.pattern, r.match_type, r.category) for r in get_category_rules(s)] == [
            ("TAMOIL", "starts_with", "Trasporti"),          # the key is in reduced words
        ]
        assert s.get(Transaction, "t2").category == "Trasporti"
        assert s.get(Transaction, "t2").category_source == "rule"
    notes = " ".join(i.value for i in app.info)
    assert "Rule: starts with TAMOIL (Certain: known merchant)." in notes
    assert "Tamoil Rozzano" in notes
    # The count survives the rerun after saving, wherever the page is scrolled.
    assert any("1" in toast.value for toast in app.toast)


def test_one_save_writes_every_ticked_rule(tmp_path, monkeypatch):
    """Every ticked row becomes a rule in the same save. The founder's save
    kept only the first: a click during the model's wait stops the run at the
    next Streamlit command, and there was one right after the first rule.
    The writes now hold none (write_and_apply returns its note instead of
    storing it); this test checks the outcome, not the interruption itself,
    which the test runner cannot simulate."""
    from ui.i18n import t
    import ui.widgets.written_rules as wr

    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        for i, name in enumerate(["Bar Uno", "Bar Due", "Bar Tre"]):
            _tx(s, f"b{i}", name, -50 - i, "2026-08-0{}".format(i + 1), category="Ristorazione")
        s.commit()
    set_language("en")
    app = _run(db_path)
    col = t("counterparts.col_categorized")
    edit = {"edited_rows": {i: {col: True} for i in range(3)}, "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    app.session_state["cp_editor"] = edit

    calls = []
    real = wr.write_and_apply

    def _spy(*a, **kw):
        calls.append(a[2])
        return real(*a, **kw)

    monkeypatch.setattr("ui.counterparts_page.write_and_apply", _spy)
    [b for b in app.button if b.key == "cp_save"][0].click().run(timeout=30)
    assert not app.exception, app.exception
    assert sorted(calls) == ["Bar Due", "Bar Tre", "Bar Uno"]
    with get_session(eng) as s:
        assert len(get_category_rules(s)) == 3


def test_card_purchases_are_spending_and_card_refunds_are_not(engine):
    """A card statement types every movement card_tx: the step used to show
    nothing for it. Purchases count as spending, refunds stay out."""
    with get_session(engine) as s:
        _tx(s, "c1", "Esselunga Corsico", -80, "2026-08-02", tx_type="card_tx")
        _tx(s, "c2", "Esselunga Corsico", 15, "2026-08-04", tx_type="card_tx")   # refund
        _tx(s, "c3", "Rimborso Amex", 30, "2026-08-05", tx_type="card_tx")
        _tx(s, "e1", "Affitto", -900, "2026-08-01", category="Casa")
        s.commit()
    svc = GuidedFlowService(engine)
    assert [(x["description"], x["total_amount"]) for x in svc.expense_counterparts()] == [
        ("Affitto", 900.0), ("Esselunga Corsico", 80.0),
    ]
    assert svc.check_progress().total == 2                 # spending movements


def test_the_workflow_looks_at_the_whole_last_import(engine):
    """A first import with two years of history shows the counterparts of the
    two years, not only the last three months (founder, 2026-10-20)."""
    from datetime import UTC, datetime, timedelta
    from db.models import ImportBatch, ImportJob

    start = datetime.now(UTC) - timedelta(minutes=30)
    with get_session(engine) as s:
        s.add(ImportJob(status="completed", progress=1, n_files=1,
                        started_at=start, completed_at=start + timedelta(minutes=20)))
        batch = ImportBatch(sha256="x" * 64, filename="storico.xlsx",
                            imported_at=start + timedelta(minutes=10), n_transactions=3)
        s.add(batch)
        s.flush()
        for tx_id, desc, day in [("o", "Coop", "2024-01-15"), ("m", "Bar", "2025-06-01"),
                                 ("n", "Affitto", "2026-08-01")]:
            _tx(s, tx_id, desc, -10, day)
            s.get(Transaction, tx_id).batch_id = batch.id
        _tx(s, "x", "Vecchio", -10, "2023-05-01")          # an earlier import: out
        s.commit()
    svc = GuidedFlowService(engine)
    assert [str(d) for d in svc.workflow_period()] == ["2024-01-15", "2026-08-01"]
    assert sorted(x["description"] for x in svc.expense_counterparts()) == ["Affitto", "Bar", "Coop"]


def _in_order(node) -> list:
    """The page's elements top to bottom."""
    children = getattr(node, "children", None)
    if not children:
        return [node]
    out = []
    for key in sorted(children):
        out += _in_order(children[key])
    return out


def test_the_rules_written_show_under_the_save_button(tmp_path):
    """Founder, 2026-10-21: the counterparts just saved were listed right
    under the page title, away from the button that was clicked."""
    from ui.i18n import t

    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    SettingsService(eng).set("country", "It")
    with get_session(eng) as s:
        _tx(s, "t1", "Tamoil Gaggiano", -60, "2026-08-01", category="Trasporti")
        _tx(s, "t2", "Esselunga Corsico", -40, "2026-08-10", category="Spesa")
        s.commit()
    set_language("en")
    app = _run(db_path)
    rows = list(app.dataframe[0].value.iloc[:, 0])
    edit = {"edited_rows": {rows.index("Tamoil Gaggiano"): {t("counterparts.col_categorized"): True}},
            "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    app.session_state["cp_editor"] = edit
    [b for b in app.button if b.key == "cp_save"][0].click().run(timeout=30)
    assert not app.exception, app.exception

    elements = _in_order(app.main)
    kinds = [type(e).__name__ for e in elements]
    note = next(i for i, e in enumerate(elements) if type(e).__name__ == "Info"
                and "TAMOIL" in e.value)
    # After the rerun the button is gone (nothing left to save): the note
    # takes its place, under the grid and the "no changes" line.
    no_changes = next(i for i, e in enumerate(elements) if type(e).__name__ == "Caption"
                      and e.value == t("counterparts.no_changes"))
    assert note > no_changes > kinds.index("Dataframe"), kinds


def test_ticking_buy_everything_writes_the_multi_category_rule(tmp_path):
    """Spec 24: the column marks a place where everything is bought; the
    counterpart leaves the step and its movements take the generic category."""
    from ui.i18n import t

    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        _tx(s, "t1", "Amazon Marketplace", -60, "2026-08-01", category="Shopping")
        _tx(s, "t2", "Amazon Marketplace", -40, "2026-08-10", category="Shopping")
        _tx(s, "t3", "Esselunga", -400, "2026-08-11", category="Spesa")
        s.commit()
    set_language("en")
    app = _run(db_path)
    rows = list(app.dataframe[0].value.iloc[:, 0])
    edit = {"edited_rows": {rows.index("Amazon Marketplace"): {t("counterparts.col_multi"): True}},
            "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    app.session_state["cp_editor"] = edit
    [b for b in app.button if b.key == "cp_save"][0].click().run(timeout=30)
    assert not app.exception, app.exception

    with get_session(eng) as s:
        [rule] = get_category_rules(s)
        assert (rule.pattern, rule.is_multi_category) == ("Amazon Marketplace", True)
        assert s.get(Transaction, "t1").category_source == "multi"
    assert "Amazon Marketplace" not in list(app.dataframe[0].value.iloc[:, 0])
