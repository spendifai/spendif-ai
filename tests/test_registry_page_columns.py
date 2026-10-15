"""The Registry table uses internal column keys, never the shown labels.

The labels moved to i18n while two lookups kept the old Italian name
"Descrizione": saving a changed category, or selecting one movement to make a
rule, crashed with KeyError. Every string key read from a table row must be
one of the page's column keys.
"""
from __future__ import annotations

import re
from pathlib import Path

import ui.registry_page as page

_SOURCE = Path(page.__file__).read_text(encoding="utf-8")
_ROW_KEY = re.compile(r'\b(?:orig|edit|_rule_tx_row|row)\[\s*"([^"]+)"\s*\]')


def test_row_lookups_use_column_keys():
    keys = {v for k, v in vars(page).items() if k.startswith("COL_") and isinstance(v, str)}
    used = set(_ROW_KEY.findall(_SOURCE))
    assert used <= keys, f"unknown column keys: {sorted(used - keys)}"
