"""Chart helpers for the single palette (spec 25): the colours come from
SettingsService.get_palette(), never from the page."""
from __future__ import annotations


def color_treemap(fig, palette, direction: str | None = None) -> None:
    """Give a category/subcategory treemap the colour of each category and
    the shades of its subcategories. Node ids are "category" and
    "category/subcategory" (plotly express with path=[category, subcategory])."""
    trace = fig.data[0]
    colors = []
    for node in trace.ids:
        category, _, sub = str(node).partition("/")
        colors.append(palette.subcategory(category, sub, direction) if sub
                      else palette.category(category, direction))
    trace.marker.colors = colors


def series_colors(palette) -> list[str]:
    """For series that are not categories (accounts, contexts): the expense
    palette, so no colour outside the app's palette appears."""
    return list(palette.series)
