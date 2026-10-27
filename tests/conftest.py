"""Shared test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_siri_folders(request, monkeypatch):
    """No test reads or creates the Siri folders of the machine running it
    (Documents, iCloud Drive): every page that opens the expense
    confirmation would otherwise touch them. A test about the path builder
    itself is marked siri_files."""
    if request.node.get_closest_marker("siri_files"):
        return
    from services.expense_confirm_service import ExpenseConfirmService
    monkeypatch.setattr(ExpenseConfirmService, "siri_files", staticmethod(lambda *args, **kwargs: []))
