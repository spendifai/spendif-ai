"""Category + subcategory as a single combined string.

Pattern: "Categoria / Sottocategoria" (separator " / ").
When the category has no subcategories the string is just "Categoria".

Public API:
    build_cat_options(taxonomy) -> list[str]
        Full flat list of valid combined strings, suitable for SelectboxColumn.

    join_cat_sub(category, subcategory) -> str
        Build the combined string from separate fields.

    split_cat_sub(value) -> (category, subcategory)
        Parse back to separate fields. Returns ("", "") for empty/None.
"""
from __future__ import annotations

from core.categorizer import TaxonomyConfig

SEP = " / "


def build_cat_options(
    taxonomy: TaxonomyConfig, *, include_empty: bool = False, income_first: bool = False,
    only: str | None = None,
) -> list[str]:
    """Return all valid category+subcategory combinations as combined strings.

    For categories with subcategories: one entry per subcategory ("Cat / Sub").
    For categories without subcategories: one entry for the category alone ("Cat").
    Spending categories come first, income first with *income_first*.
    """
    options: list[str] = []
    if include_empty:
        options.append("")
    sides = [taxonomy.all_expense_categories, taxonomy.all_income_categories]
    if income_first:
        sides.reverse()
    if only == "expense":
        sides = [taxonomy.all_expense_categories, []]
    elif only == "income":
        sides = [taxonomy.all_income_categories, []]
    for cat in sides[0] + sides[1]:
        subs = taxonomy.valid_subcategories(cat)
        if subs:
            for sub in subs:
                options.append(f"{cat}{SEP}{sub}")
        else:
            options.append(cat)
    return options


def join_cat_sub(category: str | None, subcategory: str | None) -> str:
    """Combine category and subcategory into one display string."""
    cat = (category or "").strip()
    sub = (subcategory or "").strip()
    if cat and sub:
        return f"{cat}{SEP}{sub}"
    return cat


def split_cat_sub(value: str | None) -> tuple[str, str]:
    """Parse a combined string back to (category, subcategory)."""
    if not value:
        return "", ""
    if SEP in value:
        cat, sub = value.split(SEP, 1)
        return cat.strip(), sub.strip()
    return value.strip(), ""


def cat_sub_select(label: str, taxonomy: TaxonomyConfig, category: str | None,
                   subcategory: str | None, key: str, *, only: str | None = None,
                   help: str | None = None) -> tuple[str, str]:
    """One menu for category and subcategory, as in every grid of the app:
    only valid pairs, so the subcategory always belongs to its category.
    *only* keeps one direction ("expense" or "income"). Returns
    (category, subcategory)."""
    import streamlit as st
    options = build_cat_options(taxonomy, only=only)
    if not options:
        return "", ""
    current = join_cat_sub(category, subcategory)
    if current not in options:
        # The category alone, when the subcategory is gone: its first pair.
        current = next((o for o in options if split_cat_sub(o)[0] == (category or "")), options[0])
    choice = st.selectbox(label, options, index=options.index(current), key=key, help=help)
    return split_cat_sub(choice)


def direction_of(amount) -> str:
    """"expense" for money out, "income" for money in."""
    return "expense" if float(amount or 0) < 0 else "income"


def options_for_amounts(taxonomy: TaxonomyConfig, amounts, *, include_empty: bool = False) -> list[str]:
    """The menu of a grid column: a grid shows one menu for every row, so
    with only spending rows it offers only spending categories, with only
    income rows only income ones, both when the rows are mixed (and the
    save checks each row, see wrong_direction)."""
    sides = {direction_of(a) for a in amounts}
    only = sides.pop() if len(sides) == 1 else None
    return build_cat_options(taxonomy, include_empty=include_empty, only=only)


def wrong_direction(taxonomy: TaxonomyConfig, category: str | None, amount) -> bool:
    """True when the category belongs to the other direction of the movement:
    a spending category on money in, or the other way round."""
    if not category:
        return False
    if direction_of(amount) == "expense":
        return category in taxonomy.all_income_categories and category not in taxonomy.all_expense_categories
    return category in taxonomy.all_expense_categories and category not in taxonomy.all_income_categories
