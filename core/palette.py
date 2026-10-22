"""One palette for the whole app (spec 25): the same meaning, the same colour.

Value tokens (income, expense, balance, other, status) never change. Every
category owns its colour, written once when it is born; a subcategory is a
shade of its category. Every colour keeps a contrast of at least 3:1 on both
the dark (#0e1117) and the light (#ffffff) background, so one palette serves
both themes. Pure functions, no imports from the app.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field

DARK_BACKGROUND = "#0e1117"
LIGHT_BACKGROUND = "#ffffff"
MIN_CONTRAST = 3.0

INCOME = "#2e9e5b"
EXPENSE = "#d64545"
BALANCE = "#3d8fd6"
OTHER = "#8c929b"
STATUS_OK = INCOME
STATUS_WARN = "#d9731a"
STATUS_DANGER = EXPENSE

TOKENS = {
    "income": INCOME,
    "expense": EXPENSE,
    "balance": BALANCE,
    "other": OTHER,
    "budget": BALANCE,
    "actual": EXPENSE,
    "status_ok": STATUS_OK,
    "status_warn": STATUS_WARN,
    "status_danger": STATUS_DANGER,
}

# Order of assignment: neighbours in the order are far apart in hue.
# No expense hue falls in the greens (70 to 180 degrees): they are the incomes'.
EXPENSE_COLORS = (
    "#e8684a", "#5f86b0", "#d9731a", "#9b6bc9", "#1f9fbf", "#d9608f", "#a8892a",
    "#6c7ae0", "#a0703c", "#c04fc0", "#8a8f2a", "#b0567a", "#9a86d6", "#c2665a",
)
INCOME_COLORS = ("#2f8a4e", "#5b9a2c", "#1e8c74", "#7f9f3a", "#3c9a9a", "#4a9e68")

# Relative luminance window that keeps 3:1 on both backgrounds.
_LUMINANCE_MIN = 0.125
_LUMINANCE_MAX = 0.29


def colors_for(direction: str) -> tuple[str, ...]:
    return INCOME_COLORS if direction == "income" else EXPENSE_COLORS


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in (r, g, b))


def luminance(color: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def hue(color: str) -> float:
    return colorsys.rgb_to_hls(*_rgb(color))[0] * 360


def next_color(direction: str, used: list[str]) -> str:
    """The first colour of the direction's palette not used yet; when all are
    used, the least used one (the first of them on a tie)."""
    palette = colors_for(direction)
    taken = [c.lower() for c in used if c]
    for color in palette:
        if color not in taken:
            return color
    return min(palette, key=taken.count)


def subcategory_shade(color: str, index: int, count: int) -> str:
    """The category's hue, a lightness that moves with the subcategory's
    position, always inside the contrast window. One subcategory: the
    category's colour."""
    if count <= 1:
        return color
    h, l, s = colorsys.rgb_to_hls(*_rgb(color))
    valid = [x / 200 for x in range(201)
             if _LUMINANCE_MIN <= luminance(_hex(*colorsys.hls_to_rgb(h, x / 200, s))) <= _LUMINANCE_MAX]
    if not valid:
        return color
    low, high = valid[0], valid[-1]
    step = (high - low) / (count - 1)
    return _hex(*colorsys.hls_to_rgb(h, low + step * min(index, count - 1), s))


@dataclass
class Palette:
    """The colours a chart needs: value tokens, one colour per category, the
    shades of its subcategories. Unknown names (uncategorised, "other")
    take the grey."""
    categories: dict[tuple[str, str], str] = field(default_factory=dict)
    subcategories: dict[tuple[str, str, str], str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=lambda: dict(TOKENS))
    series: tuple[str, ...] = EXPENSE_COLORS

    def category(self, name, direction: str | None = None) -> str:
        for d in ((direction,) if direction else ("expense", "income")):
            if (d, name) in self.categories:
                return self.categories[(d, name)]
        return OTHER

    def subcategory(self, category, name, direction: str | None = None) -> str:
        for d in ((direction,) if direction else ("expense", "income")):
            if (d, category, name) in self.subcategories:
                return self.subcategories[(d, category, name)]
        return self.category(category, direction)

    def category_map(self, names, direction: str | None = None) -> dict:
        return {n: self.category(n, direction) for n in names}


def build_palette(categories) -> Palette:
    """*categories*: (direction, name, colour or None, is_fallback,
    [subcategory names in order]). The fallback category is always grey."""
    palette = Palette()
    for direction, name, color, is_fallback, subs in categories:
        own = OTHER if is_fallback or not color else color
        palette.categories[(direction, name)] = own
        for i, sub in enumerate(subs):
            palette.subcategories[(direction, name, sub)] = subcategory_shade(own, i, len(subs))
    return palette
