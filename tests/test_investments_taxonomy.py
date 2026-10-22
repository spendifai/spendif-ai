"""Money set aside gets one category in both directions (spec 30): the
default trees, the move of an existing taxonomy with its movements and
rules, and how the Home and the menus treat a category used both ways."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from core.categorizer import TaxonomyConfig
from db.models import (
    BudgetTarget,
    CategoryRule,
    Transaction,
    _migrate_add_taxonomy_default,
    _migrate_add_toll_subcategory,
    _migrate_investments_and_savings,
    create_tables,
    get_session,
)
from db.taxonomy_defaults import INVESTMENTS_MOVES, TAXONOMY_DEFAULTS
from ui.home_page import _build_sankey_data, _split_flows, _t
from ui.widgets.cat_select import build_cat_options

# The Italian default tree before spec 30, only the parts that move plus one
# category that stays.
_EARLIER_IT = {
    "expense": {
        "Casa": ["Acqua", "Mutuo / Affitto"],
        "Trasporti": ["Carburante", "Parcheggio / ZTL", "Trasporto pubblico"],
        "Finanza e assicurazioni": [
            "Assicurazione vita", "Assicurazione casa", "Polizze varie",
            "Commissioni bancarie", "Investimenti / risparmio", "Fondo pensione",
            "Gestione immobili",  # created by the user
        ],
    },
    "income": {
        "Lavoro dipendente": ["Stipendio", "Bonus e premi"],
        "Lavoro autonomo": ["Fattura / parcella"],
        "Prestazioni sociali": ["Pensione / rendita", "Sussidio / bonus statale"],
        "Rendite finanziarie": ["Dividendi", "Interessi attivi", "Plusvalenze", "Cedole obbligazionarie"],
        "Rendite immobiliari": ["Affitto percepito", "Vendita immobile"],
    },
}


# ── Default trees ────────────────────────────────────────────────────────────

def _tree(lang, side):
    return {e["category"]: e["subcategories"] for e in TAXONOMY_DEFAULTS[lang][side]}


@pytest.mark.parametrize("lang", sorted(TAXONOMY_DEFAULTS))
def test_set_aside_is_one_name_both_ways_asset_out_origin_in(lang):
    both = set(_tree(lang, "expenses")) & set(_tree(lang, "income"))
    assert len(both) == 1
    (name,) = both
    assert len(_tree(lang, "expenses")[name]) == 5    # the asset
    assert len(_tree(lang, "income")[name]) == 4      # where the money comes from


@pytest.mark.parametrize("lang", sorted(INVESTMENTS_MOVES))
def test_every_move_lands_in_the_new_tree_and_leaves_the_earlier_one(lang):
    trees = {"expense": _tree(lang, "expenses"), "income": _tree(lang, "income")}
    for type_, cat, sub, new_cat, new_sub in INVESTMENTS_MOVES[lang]:
        assert new_cat in trees[type_]
        if new_sub is not None:
            assert new_sub in trees[type_][new_cat]
        if sub is None and new_cat != cat:
            assert cat not in trees[type_]


# ── Migration ────────────────────────────────────────────────────────────────

@pytest.fixture
def earlier_db():
    """A database whose taxonomy is the Italian tree before spec 30, with
    movements, rules and a budget on the categories that move."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM taxonomy_subcategory"))
        conn.execute(text("DELETE FROM taxonomy_category"))
        for type_, cats in _EARLIER_IT.items():
            for i, (cat, subs) in enumerate(cats.items()):
                conn.execute(text("INSERT INTO taxonomy_category (name, type, sort_order) VALUES (:n, :t, :s)"),
                             {"n": cat, "t": type_, "s": i})
                cat_id = conn.execute(text("SELECT id FROM taxonomy_category WHERE name = :n AND type = :t"),
                                      {"n": cat, "t": type_}).scalar()
                for j, sub in enumerate(subs):
                    conn.execute(text("INSERT INTO taxonomy_subcategory (category_id, name, sort_order) "
                                      "VALUES (:c, :n, :s)"), {"c": cat_id, "n": sub, "s": j})
    rows = [
        ("t1", -500.0, "Finanza e assicurazioni", "Fondo pensione"),
        ("t2", -2.5, "Finanza e assicurazioni", "Commissioni bancarie"),
        ("t3", -300.0, "Finanza e assicurazioni", "Assicurazione casa"),
        ("t4", 120.0, "Rendite finanziarie", "Dividendi"),
        ("t5", 4.1, "Rendite finanziarie", "Interessi attivi"),
        ("t6", 700.0, "Rendite immobiliari", "Affitto percepito"),
        ("t7", -900.0, "Finanza e assicurazioni", "Gestione immobili"),
        ("t8", 1000.0, "Lavoro autonomo", "Fattura / parcella"),
        ("t9", 1500.0, "Prestazioni sociali", "Pensione / rendita"),
    ]
    with get_session(eng) as s:
        for tx_id, amount, cat, sub in rows:
            s.add(Transaction(id=tx_id, date="2026-01-10", amount=Decimal(str(amount)), currency="EUR",
                              description="x", source_file="f.csv", doc_type="bank_statement",
                              account_label="A", tx_type="expense" if amount < 0 else "income",
                              category=cat, subcategory=sub, reconciled=False, to_review=False))
        s.add(CategoryRule(pattern="ARCA PREVIDENZA", match_type="starts_with", direction="out",
                           category="Finanza e assicurazioni", subcategory="Fondo pensione"))
        s.add(CategoryRule(pattern="AFFITTO", match_type="starts_with", direction="in",
                           category="Rendite immobiliari", subcategory="Affitto percepito"))
        s.add(BudgetTarget(category="Finanza e assicurazioni", target_pct=Decimal("5")))
        s.commit()
    return eng


def _tx(eng, tx_id):
    with eng.connect() as conn:
        return tuple(conn.execute(text('SELECT category, subcategory FROM "transaction" WHERE id = :i'),
                                  {"i": tx_id}).one())


def _taxonomy(eng):
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.type, c.name, s.name FROM taxonomy_category c "
            "LEFT JOIN taxonomy_subcategory s ON s.category_id = c.id ORDER BY c.type, c.name, s.sort_order"
        )).fetchall()
    tree: dict = {}
    for type_, cat, sub in rows:
        tree.setdefault((type_, cat), [])
        if sub:
            tree[(type_, cat)].append(sub)
    return tree


def test_movements_move_with_their_subcategory(earlier_db):
    _migrate_investments_and_savings(earlier_db)
    assert _tx(earlier_db, "t1") == ("Investimenti e risparmi", "Pensione")
    assert _tx(earlier_db, "t2") == ("Commissioni bancarie", "Canone conto e carte")
    assert _tx(earlier_db, "t3") == ("Assicurazioni", "Assicurazione casa")
    assert _tx(earlier_db, "t4") == ("Investimenti e risparmi", "Cedole e dividendi")
    assert _tx(earlier_db, "t5") == ("Investimenti e risparmi", "Interessi")
    assert _tx(earlier_db, "t6") == ("Investimenti e risparmi", "Rendita")
    assert _tx(earlier_db, "t8") == ("Lavoro autonomo", "Parcella")
    assert _tx(earlier_db, "t9") == ("Lavoro dipendente", "Pensione")


def test_the_tree_is_the_new_one_both_ways(earlier_db):
    _migrate_investments_and_savings(earlier_db)
    tree = _taxonomy(earlier_db)
    assert tree[("expense", "Investimenti e risparmi")] == ["Immobili", "Azioni", "Pensione", "Fondi", "Vincolato"]
    assert tree[("income", "Investimenti e risparmi")] == ["Rendita", "Cedole e dividendi", "Interessi",
                                                           "Vendite e riscatti"]
    assert "Pensione" in tree[("income", "Lavoro dipendente")]
    assert "Pensione / rendita" not in tree[("income", "Prestazioni sociali")]
    assert ("expense", "Finanza e assicurazioni") not in tree
    assert ("income", "Rendite finanziarie") not in tree
    assert ("income", "Rendite immobiliari") not in tree
    assert len(tree[("expense", "Commissioni bancarie")]) == 4


def test_what_the_user_created_stays_where_it_was(earlier_db):
    _migrate_investments_and_savings(earlier_db)
    assert "Gestione immobili" in _taxonomy(earlier_db)[("expense", "Assicurazioni")]
    assert _tx(earlier_db, "t7") == ("Assicurazioni", "Gestione immobili")
    assert _taxonomy(earlier_db)[("expense", "Casa")] == ["Acqua", "Mutuo / Affitto"]


def test_rules_and_budgets_follow(earlier_db):
    _migrate_investments_and_savings(earlier_db)
    with earlier_db.connect() as conn:
        rules = dict(conn.execute(text("SELECT pattern, category || ' / ' || subcategory FROM category_rule")).fetchall())
        budget = conn.execute(text("SELECT category FROM budget_target")).scalar()
    assert rules["ARCA PREVIDENZA"] == "Investimenti e risparmi / Pensione"
    assert rules["AFFITTO"] == "Investimenti e risparmi / Rendita"
    assert budget == "Assicurazioni"


def test_new_categories_get_a_colour(earlier_db):
    _migrate_investments_and_savings(earlier_db)
    with earlier_db.connect() as conn:
        missing = conn.execute(text("SELECT COUNT(*) FROM taxonomy_category "
                                    "WHERE color IS NULL AND COALESCE(is_fallback, 0) = 0")).scalar()
    assert missing == 0


def test_running_twice_changes_nothing(earlier_db):
    _migrate_investments_and_savings(earlier_db)
    first = _taxonomy(earlier_db), _tx(earlier_db, "t1")
    _migrate_investments_and_savings(earlier_db)
    assert (_taxonomy(earlier_db), _tx(earlier_db, "t1")) == first


def test_a_new_database_is_born_with_the_new_tree():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    before = _taxonomy(eng)
    _migrate_investments_and_savings(eng)
    assert _taxonomy(eng) == before
    assert ("income", "Investimenti e risparmi") in before


def test_defaults_no_longer_offer_the_earlier_categories():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO taxonomy_default (language, type, category, subcategory) "
                          "VALUES ('it', 'income', 'Rendite finanziarie', 'Dividendi')"))
    _migrate_add_taxonomy_default(eng)
    with eng.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM taxonomy_default "
                              "WHERE category = 'Rendite finanziarie'")).scalar()
    assert n == 0


# ── Home and menus ───────────────────────────────────────────────────────────

def _df(*rows):
    return pd.DataFrame(
        [(pd.Timestamp("2026-01-15"), amount, "income" if amount > 0 else "expense", cat, sub, False)
         for amount, cat, sub in rows],
        columns=["date", "amount", "tx_type", "category", "subcategory", "reconciled"],
    )


def test_money_back_from_savings_is_income_not_a_refund():
    df = _df((1000.0, "Lavoro autonomo", None), (-400.0, "Investimenti e risparmi", "Fondi"),
             (50.0, "Investimenti e risparmi", "Azioni"), (-100.0, "Casa", None), (20.0, "Casa", None))
    inc, exp = _split_flows(df, {"Casa"})
    assert inc["amount"].sum() == 1050.0          # the dividend is income
    assert exp["abs_amount"].sum() == 480.0       # 400 set aside + 100 - 20 refund


def test_the_sankey_draws_money_set_aside_last():
    df = _df((1000.0, "Lavoro autonomo", None), (-600.0, "Investimenti e risparmi", "Fondi"),
             (-100.0, "Casa", "Acqua"))
    out = _build_sankey_data(df, {"Casa"}, {"Investimenti e risparmi"})
    cats = [n for n in out["nodes"][1:] if n in ("Casa", "Investimenti e risparmi")]
    assert cats == ["Casa", "Investimenti e risparmi"]
    assert _t("home.charts.unknown") in out["nodes"]


def test_a_category_both_ways_offers_each_side_its_own_subcategories():
    tax = TaxonomyConfig(expenses={"Casa": ["Acqua"], "Investimenti e risparmi": ["Fondi"]},
                         income={"Investimenti e risparmi": ["Interessi"]})
    assert build_cat_options(tax, only="expense") == ["Casa / Acqua", "Investimenti e risparmi / Fondi"]
    assert build_cat_options(tax, only="income") == ["Investimenti e risparmi / Interessi"]
    mixed = build_cat_options(tax)
    assert mixed.count("Investimenti e risparmi / Fondi") == 1
    assert "Investimenti e risparmi / Interessi" in mixed


def test_a_pair_is_valid_on_its_own_side():
    tax = TaxonomyConfig(expenses={"Investimenti e risparmi": ["Fondi"]},
                         income={"Investimenti e risparmi": ["Interessi"]})
    assert tax.is_valid_pair("Investimenti e risparmi", "Interessi", "income")
    assert not tax.is_valid_pair("Investimenti e risparmi", "Interessi", "expense")
    assert tax.is_valid_pair("Investimenti e risparmi", "Interessi")


# ── Tolls ────────────────────────────────────────────────────────────────────

def test_tolls_join_transport_after_parking_once(earlier_db):
    _migrate_add_toll_subcategory(earlier_db)
    _migrate_add_toll_subcategory(earlier_db)
    with earlier_db.connect() as conn:
        subs = [n for (n,) in conn.execute(text(
            "SELECT s.name FROM taxonomy_subcategory s JOIN taxonomy_category c ON c.id = s.category_id "
            "WHERE c.name = 'Trasporti' ORDER BY s.sort_order"))]
    assert subs == ["Carburante", "Parcheggio / ZTL", "Pedaggio", "Trasporto pubblico"]


def test_tolls_are_in_the_italian_default():
    assert "Pedaggio" in _tree("it", "expenses")["Trasporti"]
