"""Tests for importing legacy .xls statements.

Covers:
  - load_raw_dataframe on a real .xls: the reader is installed and the header
    row is found below the bank's title rows, not taken from row 0
  - load_raw_head on the same file (schema review preview)
  - every format the upload accepts has its pandas reader declared as a
    dependency and listed in the PyInstaller hidden imports, so the packaged
    app cannot accept a file it is unable to open

The fixture is synthetic: invented rows saved as Excel 97 (.xls).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from core.normalizer import load_raw_head
from core.orchestrator import load_raw_dataframe

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_statement.xls"

# Extension accepted by the upload page -> module pandas needs to read it.
READERS = {"xls": "xlrd", "xlsx": "openpyxl"}


def test_xls_loads_with_header_below_title_rows():
    raw = FIXTURE.read_bytes()
    df, _encoding, info = load_raw_dataframe(raw, FIXTURE.name)

    assert list(df.columns) == ["Date", "Description", "Amount", "Currency"]
    assert len(df) == 4
    assert info.skipped_rows == 4
    assert info.header_certain is True


def test_xls_raw_head_preview():
    df = load_raw_head(FIXTURE.read_bytes(), FIXTURE.name, n=3)
    assert df.shape == (3, 4)


def test_accepted_formats_have_their_reader_shipped():
    upload_src = (ROOT / "ui" / "upload_page.py").read_text(encoding="utf-8")
    accepted = set(re.search(r'type=\[([^\]]*)\]', upload_src).group(1).replace('"', "").replace(" ", "").split(","))
    excel_formats = accepted - {"csv"}
    assert excel_formats <= READERS.keys(), f"no reader known for {excel_formats - READERS.keys()}"

    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    dep_names = {re.split(r"[<>=!~;\[ ]", d, maxsplit=1)[0].lower() for d in deps}
    spec_src = (ROOT / "desktop.spec").read_text(encoding="utf-8")

    for fmt in excel_formats:
        module = READERS[fmt]
        assert module in dep_names, f".{fmt} is accepted but {module} is not a dependency"
        assert f'"{module}"' in spec_src, f".{fmt} is accepted but {module} is not a hidden import"
        __import__(module)
