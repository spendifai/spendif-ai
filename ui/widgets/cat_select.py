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
