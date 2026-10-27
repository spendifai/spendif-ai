"""The Shortcuts command the app installs (spec 32, 4.9)."""
from __future__ import annotations

import plistlib

from core.siri_shortcut import build


def _actions(folder="Spendif.ai", language="it"):
    return plistlib.loads(build(folder, language))["WFWorkflowActions"]


def test_it_writes_into_the_chosen_folder():
    append = _actions("Le mie spese")[-1]["WFWorkflowActionParameters"]
    assert append["WFFilePath"] == "Le mie spese/spese-da-siri.txt"


def test_the_dictation_is_in_the_interface_language():
    assert _actions(language="it")[0]["WFWorkflowActionParameters"]["WFSpeechLanguage"] == "it-IT"
    assert _actions(language="de")[0]["WFWorkflowActionParameters"]["WFSpeechLanguage"] == "de-DE"


def test_cash_or_card_with_two_different_words():
    menu = _actions()[1]["WFWorkflowActionParameters"]
    assert menu["WFMenuPrompt"] == "Contanti o carta?" and menu["WFMenuItems"] == ["Contanti", "Carta"]
    written = [a["WFWorkflowActionParameters"]["WFTextActionText"] for a in _actions()
               if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
               and isinstance(a["WFWorkflowActionParameters"]["WFTextActionText"], str)]
    assert written == ["contanti", "carta"]          # the words the reader knows, in every language


def test_the_line_ends_with_a_new_line():
    line = [a for a in _actions() if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"][-1]
    text = line["WFWorkflowActionParameters"]["WFTextActionText"]["Value"]["string"]
    assert text.endswith("\n") and text.count(" | ") == 2


def test_the_default_name_is_not_the_apps():
    from core.siri_shortcut import default_name
    for lang in ("it", "en", "fr", "de", "es"):
        name = default_name(lang)
        assert "." not in name and "spendif" not in name.lower()
