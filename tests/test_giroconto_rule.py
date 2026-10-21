"""A counterpart can be ruled a giroconto, not only given a category.

The rule is kept like a category rule: the import turns matching movements
into internal transfers before categorization, the "apply rules" actions do
the same on stored movements, and the counterparts grid can mark and unmark
it. Synthetic data only.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, text

import core.orchestrator as orchestrator
from core.categorizer import CategorizationResult, CategoryRule, TaxonomyConfig, _try_deterministic
from core.models import CategorySource, Confidence, DocumentType, SignConvention
from core.orchestrator import ProcessingConfig, process_file
from core.schemas import DocumentSchema
from db.models import (
    Base,
    Transaction,
    _migrate_add_rule_giroconto,
    _migrate_add_rule_multi_category,
    get_session,
)
from db.repository import (
    apply_all_rules_to_all_transactions,
    apply_rules_to_review_transactions,
    create_category_rule,
    delete_giroconto_rule,
    get_category_rules,
    get_counterpart_stats,
    set_giroconto_by_rule_pattern,
)

_TAXONOMY = TaxonomyConfig(
    expenses={"Spesa": ["Supermercato"]}, income={"Entrate": ["Stipendio"]},
)


def _giro_rule(pattern: str = "Conto risparmio") -> CategoryRule:
    return CategoryRule(
        id=1, pattern=pattern, match_type="exact", category="", subcategory="",
        doc_type=None, priority=10, is_giroconto=True,
    )


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with get_session(engine) as s:
        yield s


def _tx(session, tx_id: str, description: str, amount: float, *, to_review=False) -> None:
    session.add(Transaction(
        id=tx_id, date="2026-01-01", description=description, amount=amount,
        currency="EUR", tx_type="income" if amount >= 0 else "expense",
        category="Spesa", subcategory="Supermercato", category_source="llm",
        category_confidence="medium", account_label="conto", to_review=to_review,
    ))
    session.flush()


# ── Migration ─────────────────────────────────────────────────────────────────

def test_migration_adds_the_column_to_an_old_database():
    eng = create_engine("sqlite:///:memory:")
    with eng.connect() as conn:
        conn.execute(text(
            "CREATE TABLE category_rule (id INTEGER PRIMARY KEY, pattern TEXT NOT NULL,"
            " match_type VARCHAR(10) NOT NULL, category VARCHAR(128) NOT NULL,"
            " subcategory VARCHAR(128), context VARCHAR(64), doc_type VARCHAR(32),"
            " priority INTEGER, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO category_rule (pattern, match_type, category, priority)"
            " VALUES ('Coop', 'exact', 'Spesa', 0)"
        ))
        conn.commit()

    _migrate_add_rule_giroconto(eng)
    _migrate_add_rule_giroconto(eng)  # idempotent
    _migrate_add_rule_multi_category(eng)                # the later column (spec 24)

    assert "is_giroconto" in {c["name"] for c in inspect(eng).get_columns("category_rule")}
    with get_session(eng) as s:
        [rule] = get_category_rules(s)
    assert rule.is_giroconto is False


# ── Categorization never uses a giroconto rule as a category ─────────────────

def test_categorizer_skips_giroconto_rules():
    rules = [_giro_rule(), CategoryRule(
        id=2, pattern="Conto risparmio", match_type="exact", category="Spesa",
        subcategory="Supermercato", doc_type=None, priority=0,
    )]
    result = _try_deterministic("Conto risparmio", Decimal("-100"), "bank_account", rules, _TAXONOMY)
    assert result.category == "Spesa"


# ── Import ────────────────────────────────────────────────────────────────────

def _statement_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Descrizione", "Importo"])
    w.writerow(["02/01/2026", "Conto risparmio", "-500,00"])
    w.writerow(["03/01/2026", "Supermercato", "-40,00"])
    w.writerow(["04/01/2026", "Conto risparmio", "200,00"])
    return buf.getvalue().encode("utf-8")


def test_import_turns_ruled_movements_into_giroconti(monkeypatch):
    schema = DocumentSchema(
        doc_type=DocumentType.bank_account, date_col="Data", amount_col="Importo",
        description_col="Descrizione", sign_convention=SignConvention.signed_single,
        date_format="%d/%m/%Y", account_label="conto", confidence=Confidence.high,
        confidence_score=1.0,
    )
    categorized: list[str] = []

    def _categorize(transactions, **kwargs):
        categorized.extend(t["description"] for t in transactions)
        return [CategorizationResult(
            category="Spesa", subcategory="Supermercato",
            confidence=Confidence.high, source=CategorySource.rule,
        ) for _ in transactions]

    monkeypatch.setattr(orchestrator, "_build_backend", lambda config: None)
    monkeypatch.setattr(orchestrator, "_build_phase_backend", lambda config, phase: None)
    monkeypatch.setattr(orchestrator, "classify_document", lambda **kwargs: schema)
    monkeypatch.setattr(orchestrator, "clean_descriptions_batch", lambda txs, **kwargs: txs)
    monkeypatch.setattr(orchestrator, "categorize_batch", _categorize)

    result = process_file(
        _statement_csv(), "statement.csv", ProcessingConfig(), _TAXONOMY, [_giro_rule()],
    )

    assert len(result.transactions) == 3
    ruled = [t for t in result.transactions if t["description"].casefold() == "conto risparmio"]
    assert sorted(t["tx_type"] for t in ruled) == ["internal_in", "internal_out"]
    assert all(t["category"] is None for t in ruled)
    assert [d.casefold() for d in categorized] == ["supermercato"]


# ── Apply rules to stored movements ──────────────────────────────────────────

def test_apply_rules_marks_stored_movements(session):
    _tx(session, "a", "Conto risparmio", -500.0, to_review=True)
    _tx(session, "b", "Conto risparmio", 200.0)
    _tx(session, "c", "Supermercato", -40.0, to_review=True)

    assert apply_rules_to_review_transactions(session, [_giro_rule()]) == 1
    assert session.get(Transaction, "a").tx_type == "internal_out"
    assert session.get(Transaction, "a").to_review is False

    assert apply_all_rules_to_all_transactions(session, [_giro_rule()]) == (1, 0)
    assert session.get(Transaction, "b").tx_type == "internal_in"
    assert session.get(Transaction, "c").tx_type == "expense"


def test_mark_and_unmark_existing_movements(session):
    _tx(session, "a", "Conto risparmio", -500.0)
    _tx(session, "b", "CONTO RISPARMIO", 200.0)

    assert set_giroconto_by_rule_pattern(session, "conto risparmio", "exact", True) == 2
    stats = get_counterpart_stats(session, tx_types=("expense", "income", "internal_out", "internal_in"))
    assert [s["is_giroconto"] for s in stats] == [True]
    # Hidden from the default view, which counts expenses and income only.
    assert get_counterpart_stats(session) == []

    assert set_giroconto_by_rule_pattern(session, "conto risparmio", "exact", False) == 2
    assert session.get(Transaction, "a").tx_type == "expense"
    assert session.get(Transaction, "b").tx_type == "income"


def test_one_rule_per_counterpart(session):
    create_category_rule(session, "Conto risparmio", "exact", "", "", priority=10, is_giroconto=True)
    # The same counterpart given a category replaces the giroconto rule.
    _, created = create_category_rule(session, "Conto risparmio", "exact", "Spesa", "Supermercato")
    assert created is False
    [rule] = get_category_rules(session)
    assert rule.is_giroconto is False and rule.category == "Spesa"

    assert delete_giroconto_rule(session, "Conto risparmio", "exact") is False
    create_category_rule(session, "Conto risparmio", "exact", "", "", is_giroconto=True)
    assert delete_giroconto_rule(session, "conto risparmio", "exact") is True
    assert get_category_rules(session) == []


# ── The counterparts grid, executed in a real Streamlit runtime ──────────────

def _grid_script(db_path: str) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    from sqlalchemy import create_engine

    from ui.counterparts_page import render_counterparts_page

    render_counterparts_page(create_engine(f"sqlite:///{db_path}"))


def _grid(db_path):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_grid_script, kwargs={"db_path": str(db_path)})
    app.run(timeout=30)
    assert not app.exception, app.exception
    return app


# The grid sorts by number of movements, most first: the two-movement
# counterpart is row 0.
_ROW = 0


def _tick_and_save(app, value: bool) -> None:
    """Tick (or untick) the giroconto cell of row _ROW, then press save.

    AppTest cannot type into a data editor, so the edit goes in as the
    editor's own state, the way the browser would send it, on every run.
    """
    from ui.i18n import t

    edit = {"edited_rows": {_ROW: {t("counterparts.col_giroconto"): value}},
            "added_rows": [], "deleted_rows": []}
    app.session_state["cp_editor"] = edit
    app.run(timeout=30)
    assert not app.exception, app.exception
    save = [b for b in app.button if b.key == "cp_save"]
    assert save, "the edit was not seen as a change"
    app.session_state["cp_editor"] = edit
    save[0].click().run(timeout=30)
    assert not app.exception, app.exception


def test_grid_marks_and_unmarks_a_counterpart(tmp_path):
    from db.models import create_tables

    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        _tx(s, "a", "Conto risparmio", -500.0)
        _tx(s, "b", "Conto risparmio", 200.0)
        _tx(s, "c", "Supermercato", -40.0)
        s.commit()

    _tick_and_save(_grid(db_path), True)

    with get_session(eng) as s:
        rules = get_category_rules(s)
        assert [(r.pattern, r.is_giroconto) for r in rules] == [("Conto Risparmio", True)]
        assert s.get(Transaction, "a").tx_type == "internal_out"
        assert s.get(Transaction, "b").tx_type == "internal_in"
        assert s.get(Transaction, "c").tx_type == "expense"

    _tick_and_save(_grid(db_path), False)

    with get_session(eng) as s:
        assert get_category_rules(s) == []
        assert s.get(Transaction, "a").tx_type == "expense"
        assert s.get(Transaction, "b").tx_type == "income"


def _rules_script(db_path: str) -> None:
    """Runs inside a real Streamlit runtime, not in the test process."""
    from sqlalchemy import create_engine

    from ui.rules_page import render_rules_page

    render_rules_page(create_engine(f"sqlite:///{db_path}"))


def test_rules_page_shows_a_giroconto_rule(tmp_path):
    from streamlit.testing.v1 import AppTest

    from db.models import create_tables
    from ui.i18n import t

    db_path = tmp_path / "ledger.db"
    eng = create_tables(create_engine(f"sqlite:///{db_path}"))
    with get_session(eng) as s:
        rule, _ = create_category_rule(s, "Conto risparmio", "exact", "", "", is_giroconto=True)
        s.commit()
        rule_id = rule.id

    app = AppTest.from_function(_rules_script, kwargs={"db_path": str(db_path)})
    app.run(timeout=30)
    assert not app.exception, app.exception
    assert any(t("rules.giroconto_value") in o for o in app.selectbox(key="rules_select").options)
    assert app.checkbox(key=f"rule_edit_giro_{rule_id}").value is True
