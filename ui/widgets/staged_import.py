"""Files prepared account by account in the Check List, waiting for the import.

The Check List knows which account each file belongs to, so it picks the files
there; the Import page then only reads, adjusts header and footer, runs and
reports. The files live in the session until the import starts.
"""
from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from ui.i18n import t

_KEY = "_staged_import_files"
_ROWS_KEY = "_staged_import_rows"


@dataclass
class StagedFile:
    name: str
    data: bytes
    account: str
    format_owner: str | None = None   # account the file's format is already linked to


def staged_files() -> list[StagedFile]:
    return list(st.session_state.get(_KEY, []))


def stage_file(f: StagedFile) -> bool:
    """Put ``f`` on hold. False when a file with the same name is already on
    hold for another account: the import tells files apart by name."""
    files = staged_files()
    for other in files:
        if other.name == f.name:
            if other.account != f.account:
                return False
            files.remove(other)   # same file picked again for the same account
            break
    files.append(f)
    st.session_state[_KEY] = files
    return True


def unstage_file(name: str) -> None:
    st.session_state[_KEY] = [f for f in staged_files() if f.name != name]


def take_staged_files() -> list[StagedFile]:
    """Return the files on hold and clear them: they are handed to one import."""
    return st.session_state.pop(_KEY, [])


def files_and_rows(n_files: int, n_rows: int) -> str:
    """"6 file per un totale di 223 righe": how much there is to import."""
    files = t("common.files_one" if n_files == 1 else "common.files", n=n_files)
    rows = t("common.rows_one" if n_rows == 1 else "common.rows", n=n_rows)
    return t("common.files_and_rows", files=files, rows=rows)


def staged_rows(count_rows) -> int:
    """Rows of the files on hold, counted once per set of files.

    Counting reads every file; the Check List redraws often, so the count is
    kept until the files on hold change. *count_rows* takes [(name, bytes)].
    """
    files = staged_files()
    key = tuple((f.name, len(f.data)) for f in files)
    cached = st.session_state.get(_ROWS_KEY)
    if cached and cached[0] == key:
        return cached[1]
    n = count_rows([(f.name, f.data) for f in files])
    st.session_state[_ROWS_KEY] = (key, n)
    return n
