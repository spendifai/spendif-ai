"""The category says why the money went, the context the occasion: the
default trees without occasion subcategories, and the move of an existing
taxonomy with its movements, rules, corrections and OSM mappings."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from db.models import (
    CategoryRule,
    Transaction,
    _migrate_meals_and_lodging,
    _migrate_split_fuel,
    create_tables,
    get_session,
)
from db.taxonomy_defaults import FUEL_SPLIT, MEALS_LODGING_MOVES, TAXONOMY_DEFAULTS

# The Italian default tree before the move, only the parts that move plus
# one category the user created.
_EARLIER_IT = {
    "Ristorazione": ["Ristorante", "Bar / caffè", "Asporto / delivery"],
    "Trasporti": ["Carburante", "Trasporto pubblico", "Taxi / ride-sharing", "Altro trasporti"],
    "Svago e tempo libero": ["Sport e palestra", "Viaggi e vacanze", "Hobby"],
    "Gite": ["Rifugi"],  # created by the user
}


def _tree(lang):
    return {e["category"]: e["subcategories"] for e in TAXONOMY_DEFAULTS[lang]["expenses"]}


# ── Default trees ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", sorted(TAXONOMY_DEFAULTS))
def test_no_default_subcategory_names_an_occasion(lang):
    m = MEALS_LODGING_MOVES[lang]
    tree = _tree(lang)
    assert m["dining"] not in tree
    assert tree[m["meals"]] == [m["eating"], m["lodging"]]
    assert m["lodging"] in tree[m["meals"]]
    assert m["travel"] not in tree[m["leisure"]]
    assert m["carried"] in tree[m["transport"]]
    assert m["public"] not in tree[m["transport"]] and m["taxi"] not in tree[m["transport"]]


def test_every_language_has_its_moves():
    assert set(MEALS_LODGING_MOVES) == set(TAXONOMY_DEFAULTS)


# ── Migration ────────────────────────────────────────────────────────────────

@pytest.fixture
def earlier_db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM taxonomy_subcategory"))
        conn.execute(text("DELETE FROM taxonomy_category"))
        for i, (cat, subs) in enumerate(_EARLIER_IT.items()):
            conn.execute(text("INSERT INTO taxonomy_category (name, type, sort_order) VALUES (:n, 'expense', :s)"),
                         {"n": cat, "s": i})
            cat_id = conn.execute(text("SELECT id FROM taxonomy_category WHERE name = :n AND type = 'expense'"),
                                  {"n": cat}).scalar()
            for j, sub in enumerate(subs):
                conn.execute(text("INSERT INTO taxonomy_subcategory (category_id, name, sort_order) "
                                  "VALUES (:c, :n, :s)"), {"c": cat_id, "n": sub, "s": j})
        conn.execute(text("INSERT OR REPLACE INTO user_settings (key, value) VALUES ('contexts', :v)"),
                     {"v": json.dumps(["Quotidianità", "Lavoro", "Vacanza"])})
        for tag, sub in (("tourism=hotel", "Viaggi e vacanze"), ("office=travel_agent", "Viaggi e vacanze"),
                         ("leisure=park", "Viaggi e vacanze")):
            conn.execute(text("INSERT INTO nsi_tag_mapping (osm_tag, category, subcategory, taxonomy_hash) "
                              "VALUES (:t, 'Svago e tempo libero', :s, 'h')"), {"t": tag, "s": sub})
    rows = [
        ("r1", "Ristorazione", "Ristorante", None),
        ("d1", "Ristorazione", "Asporto / delivery", None),
        ("b1", "Trasporti", "Trasporto pubblico", None),
        ("x1", "Trasporti", "Taxi / ride-sharing", None),
        ("h1", "Svago e tempo libero", "Viaggi e vacanze", None),
        ("h2", "Svago e tempo libero", "Viaggi e vacanze", "Lavoro"),
        ("g1", "Gite", "Rifugi", None),
    ]
    with get_session(eng) as s:
        for tx_id, cat, sub, ctx in rows:
            s.add(Transaction(id=tx_id, date="2026-01-10", amount=Decimal("-50"), currency="EUR",
                              description=f"x {tx_id}", source_file="f.csv", doc_type="bank_statement",
                              account_label="A", tx_type="expense", category=cat, subcategory=sub,
                              category_source="rule", model_category=cat, model_subcategory=sub,
                              context=ctx, reconciled=False, to_review=False))
        s.add(CategoryRule(pattern="BOOKING", match_type="starts_with", direction="out",
                           category="Svago e tempo libero", subcategory="Viaggi e vacanze"))
        s.add(CategoryRule(pattern="PIZZERIA", match_type="starts_with", direction="out",
                           category="Ristorazione", subcategory="Ristorante"))
        s.add(CategoryRule(pattern="ATM MILANO", match_type="starts_with", direction="out",
                           category="Trasporti", subcategory="Trasporto pubblico"))
        s.commit()
    return eng


def _one(eng, sql, **params):
    with eng.connect() as conn:
        return conn.execute(text(sql), params).fetchall()


def _tx(eng, tx_id):
    return tuple(_one(eng, 'SELECT category, subcategory, category_source, context, to_review, '
                           'model_category FROM "transaction" WHERE id = :i', i=tx_id)[0])


def _subs(eng, cat):
    return [n for (n,) in _one(eng, "SELECT s.name FROM taxonomy_subcategory s JOIN taxonomy_category c "
                                    "ON c.id = s.category_id WHERE c.name = :c ORDER BY s.sort_order", c=cat)]


def test_dining_becomes_meals_and_lodging_with_its_movements_and_rules(earlier_db):
    _migrate_meals_and_lodging(earlier_db)
    assert _subs(earlier_db, "Ristorazione") == []
    assert _subs(earlier_db, "Vitto e alloggio") == ["Pasti", "Albergo / B&B"]
    # Restaurant, bar and delivery alike: food someone else prepared.
    assert _tx(earlier_db, "r1")[:2] == ("Vitto e alloggio", "Pasti")
    assert _tx(earlier_db, "d1")[:2] == ("Vitto e alloggio", "Pasti")
    assert _one(earlier_db, "SELECT category, subcategory FROM category_rule WHERE pattern = 'PIZZERIA'") == \
        [("Vitto e alloggio", "Pasti")]


def test_bus_and_taxi_are_one_subcategory_in_the_place_of_public_transport(earlier_db):
    _migrate_meals_and_lodging(earlier_db)
    assert _subs(earlier_db, "Trasporti") == ["Carburante", "Mezzi pubblici e taxi", "Altro trasporti"]
    assert _tx(earlier_db, "b1")[:2] == ("Trasporti", "Mezzi pubblici e taxi")
    assert _tx(earlier_db, "x1")[:2] == ("Trasporti", "Mezzi pubblici e taxi")
    assert _one(earlier_db, "SELECT subcategory FROM category_rule WHERE pattern = 'ATM MILANO'") == \
        [("Mezzi pubblici e taxi",)]


def test_travel_goes_its_movements_back_to_review_with_the_holiday_context(earlier_db):
    _migrate_meals_and_lodging(earlier_db)
    assert _subs(earlier_db, "Svago e tempo libero") == ["Sport e palestra", "Hobby"]
    assert _tx(earlier_db, "h1") == (None, None, None, "Vacanza", 1, None)
    # A context the user had set is the user's: not overwritten.
    assert _tx(earlier_db, "h2")[3:5] == ("Lavoro", 1)


def test_travel_rules_go_and_its_osm_mappings_move_to_the_hotel(earlier_db):
    _migrate_meals_and_lodging(earlier_db)
    assert _one(earlier_db, "SELECT COUNT(*) FROM category_rule WHERE pattern = 'BOOKING'") == [(0,)]
    mappings = dict((t, (c, s)) for t, c, s in _one(
        earlier_db, "SELECT osm_tag, category, subcategory FROM nsi_tag_mapping"))
    assert mappings == {"tourism=hotel": ("Vitto e alloggio", "Albergo / B&B"),
                        "office=travel_agent": ("Vitto e alloggio", "Albergo / B&B")}


def test_categories_the_user_created_are_not_touched(earlier_db):
    _migrate_meals_and_lodging(earlier_db)
    assert _subs(earlier_db, "Gite") == ["Rifugi"]
    assert _tx(earlier_db, "g1")[:2] == ("Gite", "Rifugi")


def test_running_twice_changes_nothing_more(earlier_db):
    _migrate_meals_and_lodging(earlier_db)
    first = _one(earlier_db, "SELECT c.name, s.name FROM taxonomy_category c "
                             "LEFT JOIN taxonomy_subcategory s ON s.category_id = c.id ORDER BY c.name, s.sort_order")
    _migrate_meals_and_lodging(earlier_db)
    assert _one(earlier_db, "SELECT c.name, s.name FROM taxonomy_category c "
                            "LEFT JOIN taxonomy_subcategory s ON s.category_id = c.id ORDER BY c.name, s.sort_order") \
        == first


def test_a_new_database_starts_from_the_new_tree():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    create_tables(eng)
    assert "Albergo / B&B" in _subs(eng, "Vitto e alloggio")
    assert "Viaggi e vacanze" not in _subs(eng, "Svago e tempo libero")


# ── Fuel ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", sorted(TAXONOMY_DEFAULTS))
def test_fuel_is_split_by_what_goes_into_the_vehicle(lang):
    f = FUEL_SPLIT[lang]
    subs = _tree(lang)[f["transport"]]
    assert f["fuel"] not in subs
    assert subs[:2] == [f["fossil"], f["electric"]]


def test_fuel_keeps_its_movements_and_rules_and_charging_follows_it(earlier_db):
    with get_session(earlier_db) as s:
        s.add(Transaction(id="f1", date="2026-01-10", amount=Decimal("-60"), currency="EUR",
                          description="eni", source_file="f.csv", doc_type="bank_statement",
                          account_label="A", tx_type="expense", category="Trasporti",
                          subcategory="Carburante", reconciled=False, to_review=False))
        s.add(CategoryRule(pattern="ENI", match_type="starts_with", direction="out",
                           category="Trasporti", subcategory="Carburante"))
        s.commit()
    _migrate_meals_and_lodging(earlier_db)
    _migrate_split_fuel(earlier_db)
    _migrate_split_fuel(earlier_db)
    assert _subs(earlier_db, "Trasporti") == ["Carburante gasolio / benzina / GPL", "Ricarica elettrica",
                                              "Mezzi pubblici e taxi", "Altro trasporti"]
    assert _tx(earlier_db, "f1")[:2] == ("Trasporti", "Carburante gasolio / benzina / GPL")
    assert _one(earlier_db, "SELECT subcategory FROM category_rule WHERE pattern = 'ENI'") == \
        [("Carburante gasolio / benzina / GPL",)]
