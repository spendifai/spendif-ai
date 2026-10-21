"""Single palette (spec 25): one meaning, one colour, everywhere."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.palette import (
    DARK_BACKGROUND,
    EXPENSE_COLORS,
    INCOME_COLORS,
    LIGHT_BACKGROUND,
    MIN_CONTRAST,
    OTHER,
    TOKENS,
    build_palette,
    contrast,
    hue,
    luminance,
    next_color,
    subcategory_shade,
)
from db.models import Base, TaxonomyCategory, _migrate_add_category_color
from db import repository


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ── Palette ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("color", EXPENSE_COLORS + INCOME_COLORS + tuple(TOKENS.values()))
def test_every_colour_reads_on_both_themes(color):
    assert contrast(color, DARK_BACKGROUND) >= MIN_CONTRAST
    assert contrast(color, LIGHT_BACKGROUND) >= MIN_CONTRAST


def test_no_expense_hue_among_the_greens():
    assert all(not 70 <= hue(c) <= 180 for c in EXPENSE_COLORS)


def test_palettes_have_no_repeats():
    assert len(set(EXPENSE_COLORS)) == len(EXPENSE_COLORS)
    assert len(set(INCOME_COLORS)) == len(INCOME_COLORS)


def test_next_colour_is_the_first_free_then_the_least_used():
    assert next_color("expense", []) == EXPENSE_COLORS[0]
    assert next_color("expense", [EXPENSE_COLORS[0]]) == EXPENSE_COLORS[1]
    assert next_color("income", []) == INCOME_COLORS[0]
    used = list(EXPENSE_COLORS) + [EXPENSE_COLORS[0]]
    assert next_color("expense", used) == EXPENSE_COLORS[1]


def test_subcategory_shades_keep_hue_and_contrast():
    base = EXPENSE_COLORS[0]
    shades = [subcategory_shade(base, i, 6) for i in range(6)]
    assert len(set(shades)) == 6
    assert sorted(shades, key=luminance) == shades
    for s in shades:
        assert abs(hue(s) - hue(base)) < 3
        assert contrast(s, DARK_BACKGROUND) >= MIN_CONTRAST
        assert contrast(s, LIGHT_BACKGROUND) >= MIN_CONTRAST


def test_one_subcategory_takes_the_category_colour():
    assert subcategory_shade(INCOME_COLORS[2], 0, 1) == INCOME_COLORS[2]


def test_fallback_and_unknown_names_are_grey():
    palette = build_palette([
        ("expense", "Casa", EXPENSE_COLORS[0], False, ["Affitto", "Mutuo"]),
        ("expense", "Altro", EXPENSE_COLORS[1], True, ["Spese non classificate"]),
    ])
    assert palette.category("Casa") == EXPENSE_COLORS[0]
    assert palette.category("Altro") == OTHER
    assert palette.category("Non categorizzato") == OTHER
    assert palette.subcategory("Casa", "Affitto") != palette.subcategory("Casa", "Mutuo")
    assert palette.subcategory("Casa", "Sconosciuta") == EXPENSE_COLORS[0]


# ── Storage ──────────────────────────────────────────────────────────────────

def _color(session, name):
    session.expire_all()
    return session.query(TaxonomyCategory).filter_by(name=name).one().color


def test_a_new_category_is_born_with_the_first_free_colour(session):
    repository.create_taxonomy_category(session, "Casa", "expense")
    repository.create_taxonomy_category(session, "Cibo", "expense")
    repository.create_taxonomy_category(session, "Stipendio", "income")
    assert _color(session, "Casa") == EXPENSE_COLORS[0]
    assert _color(session, "Cibo") == EXPENSE_COLORS[1]
    assert _color(session, "Stipendio") == INCOME_COLORS[0]


def test_adding_or_removing_does_not_move_the_others(session):
    a = repository.create_taxonomy_category(session, "A", "expense")
    repository.create_taxonomy_category(session, "B", "expense")
    repository.delete_taxonomy_category(session, a.id)
    repository.create_taxonomy_category(session, "C", "expense")
    assert _color(session, "B") == EXPENSE_COLORS[1]
    assert _color(session, "C") == EXPENSE_COLORS[0]  # A's colour was freed


def test_the_users_colour_is_never_overwritten(session):
    a = repository.create_taxonomy_category(session, "A", "expense")
    assert repository.set_taxonomy_category_color(session, a.id, "#123ABC")
    repository.create_taxonomy_category(session, "B", "expense")
    assert _color(session, "A") == "#123abc"
    assert not repository.set_taxonomy_category_color(session, a.id, "red")


def test_migration_colours_existing_categories_once_in_sort_order():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        TaxonomyCategory(name="Second", type="expense", sort_order=2),
        TaxonomyCategory(name="First", type="expense", sort_order=1),
        TaxonomyCategory(name="Other", type="expense", sort_order=3, is_fallback=True),
    ])
    s.commit()
    _migrate_add_category_color(engine)
    _migrate_add_category_color(engine)  # idempotent
    assert _color(s, "First") == EXPENSE_COLORS[0]
    assert _color(s, "Second") == EXPENSE_COLORS[1]
    assert _color(s, "Other") is None
    s.close()


# ── No page picks its own colours ────────────────────────────────────────────

_CHART_FILES = (
    "ui/home_page.py", "ui/analysis_page.py", "ui/report_page.py",
    "ui/budget_vs_actual_page.py", "ui/llm_models_page.py", "reports/generator.py",
)


@pytest.mark.parametrize("path", _CHART_FILES)
def test_chart_pages_take_colours_from_the_palette(path):
    source = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{6}\b", source)
    assert "px.colors" not in source
    assert "color_continuous_scale" not in source
