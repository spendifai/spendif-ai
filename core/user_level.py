"""The user's level and the menu it shows (spec 19).

The level serves the menu, it never judges the user (spec 19, 2.1): four
levels, the last on two independent axes, money (analysing) and data
(refining). A level includes all the ones below it. Nothing is out of reach:
the pages above the level stay one click away under "Other entries".
"""
from __future__ import annotations

MONEY = "money"
DATA = "data"
AXES = (MONEY, DATA)

# Page key -> (level, axis of level 4 or None). Spec 19, 3 and 11.1.
PAGE_LEVEL: dict[str, tuple[int, str | None]] = {
    "expense_confirm":  (1, None),
    "chat":             (1, None),
    "home":             (1, None),
    "check":            (2, None),
    "checklist":        (2, None),
    "import":           (2, None),
    "ledger":           (2, None),
    "counterparts":     (2, None),
    "report":           (2, None),
    "settings":         (3, None),
    "taxonomy":         (3, None),
    "rules":            (3, None),
    "llm_models":       (3, None),
    "analytics":        (4, MONEY),
    "budget":           (4, MONEY),
    "budget_vs_actual": (4, MONEY),
    "bulk_edit":        (4, DATA),
    "review":           (4, DATA),
    "history":          (4, DATA),
    "diagnostics":      (4, DATA),
    "intermediaries":   (4, DATA),
}

# The first day's menu, in this order: the Home last, where the day ends
# (spec 19, 3).
_LEVEL_ONE_ORDER = ("expense_confirm", "chat", "home")


def is_visible(page: str, level: int, axes: frozenset[str] | set[str]) -> bool:
    """True when *page* is in the menu at *level* with the level-4 *axes*.
    A page with no level (a new one) is shown: hiding is a choice, not a
    default."""
    if page not in PAGE_LEVEL:
        return True
    needed, axis = PAGE_LEVEL[page]
    if needed < 4:
        return level >= needed
    return level >= 4 and axis in axes


def menu(pages: list[str], level: int, axes: frozenset[str] | set[str]) -> tuple[list[str], list[str]]:
    """The menu at *level*: the pages shown, in order, and the others, for
    "Other entries". *pages* is the full menu in its usual order."""
    shown = [p for p in pages if is_visible(p, level, axes)]
    others = [p for p in pages if p not in shown]
    if level == 1:
        shown = [p for p in _LEVEL_ONE_ORDER if p in shown] + [p for p in shown if p not in _LEVEL_ONE_ORDER]
    return shown, others


def level_from_data(n_movements: int, n_rules: int, n_budgets: int) -> tuple[int, frozenset[str]]:
    """The level the data already show (spec 19, 11.4), for whoever comes
    from a version without levels: no one loses pages they used. A rule
    exists only by the user's choice ("Sempre cosi'" or the Rules page)."""
    axes = frozenset({MONEY}) if n_budgets else frozenset()
    if n_budgets:
        return 4, axes
    if n_rules:
        return 3, axes
    if n_movements:
        return 2, axes
    return 1, axes


def parse_axes(text: str | None) -> frozenset[str]:
    """The axes as stored in the settings, "money,data"."""
    return frozenset(a for a in (text or "").split(",") if a in AXES)


def format_axes(axes: frozenset[str] | set[str]) -> str:
    return ",".join(a for a in AXES if a in axes)
