"""UserLevelService: the level the menu shows (spec 19).

The level is kept in the settings: `user_level` and `user_level_axes`, never
going down by themselves, and `user_level_override`, the user's own choice
from the Settings page, which wins in both directions (spec 19, 2.2-2.3).
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from core.user_level import format_axes, level_from_data, menu, parse_axes
from db import repository
from db.models import BudgetTarget, CategoryRule, Transaction

LEVEL = "user_level"
AXES = "user_level_axes"
OVERRIDE = "user_level_override"      # "", or "<level>:<axes>", e.g. "4:money,data"


class UserLevelService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
        finally:
            s.close()

    def level(self) -> tuple[int, frozenset[str]]:
        """The level and the level-4 axes the menu shows: the user's own
        choice when there is one, otherwise the level reached, which the data
        can raise and nothing lowers."""
        with self._session() as s:
            override = repository.get_user_setting(s, OVERRIDE, "") or ""
            if override:
                return _parse_override(override)
            stored = int(repository.get_user_setting(s, LEVEL, "1") or 1)
            stored_axes = parse_axes(repository.get_user_setting(s, AXES, ""))
            data_level, data_axes = level_from_data(
                s.query(func.count(Transaction.id)).scalar() or 0,
                s.query(func.count(CategoryRule.id)).scalar() or 0,
                s.query(func.count(BudgetTarget.id)).scalar() or 0,
            )
            level, axes = max(stored, data_level), stored_axes | data_axes
            if (level, axes) != (stored, stored_axes):
                repository.set_user_setting(s, LEVEL, str(level))
                repository.set_user_setting(s, AXES, format_axes(axes))
                s.commit()
            return level, axes

    def menu(self, pages: list[str]) -> tuple[list[str], list[str]]:
        """The pages the menu shows at the user's level, in order, and the
        others, one click away under "Other entries"."""
        level, axes = self.level()
        return menu(pages, level, axes)

    def override(self) -> tuple[int, frozenset[str]] | None:
        """The user's own choice, or None for automatic."""
        with self._session() as s:
            text = repository.get_user_setting(s, OVERRIDE, "") or ""
        return _parse_override(text) if text else None

    def set_override(self, level: int | None, axes: frozenset[str] | set[str] = frozenset()) -> None:
        """The user's choice of level; None goes back to automatic."""
        value = "" if level is None else f"{level}:{format_axes(axes if level == 4 else frozenset())}"
        with self._session() as s:
            repository.set_user_setting(s, OVERRIDE, value)
            s.commit()


def _parse_override(text: str) -> tuple[int, frozenset[str]]:
    level, _, axes = text.partition(":")
    return max(1, min(4, int(level))), parse_axes(axes)
