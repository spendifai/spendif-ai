"""The Shortcuts command "Segna spesa" (spec 32, 4.9), built as a plist.

The app builds it with the folder the user chose and signs it on the Mac
(`shortcuts sign`); Shortcuts then shows it and the user adds it with one
click. Pure: no file is written here.

The command dictates the expense in the interface language, asks "cash or
card?" with two long, different words (a spoken "yes" was hard for Siri to
catch), and appends one line to the inbox file in the Shortcuts folder of
iCloud Drive: "AAAA-MM-GG HH:MM | contanti | <sentence>" and a new line.
"""
from __future__ import annotations

import plistlib
import uuid

from core.siri_inbox import INBOX_NAME

# The default name, the phrase said to Siri, in the interface language. No
# dot, which no voice says the way it is written, and not the app's name:
# "Spesa Spendifai" made Siri open the app instead (founder, 2026-10-27).
# The user can change it.
_DEFAULT_NAMES = {"it": "Segna spesa", "en": "Log expense", "fr": "Noter une dépense",
                  "de": "Ausgabe notieren", "es": "Anotar gasto"}
SHORTCUT_NAME = _DEFAULT_NAMES["it"]


def default_name(language: str) -> str:
    """The command's default name in *language*.

    Args:
        language: the interface language code.
    """
    return _DEFAULT_NAMES.get(language, _DEFAULT_NAMES["en"])

# Language of the dictation, and the question with its two answers.
_SPEECH = {"it": "it-IT", "en": "en-US", "fr": "fr-FR", "de": "de-DE", "es": "es-ES"}
_ASK = {"it": ("Contanti o carta?", "Contanti", "Carta"),
        "en": ("Cash or card?", "Cash", "Card"),
        "fr": ("Espèces ou carte ?", "Espèces", "Carte"),
        "de": ("Bar oder Karte?", "Bar", "Karte"),
        "es": ("¿Efectivo o tarjeta?", "Efectivo", "Tarjeta")}
_OBJECT = "￼"          # where a variable sits in a Shortcuts text


def _token(parts: list) -> dict:
    """A Shortcuts text with variables in it.

    Args:
        parts: plain strings, and (output UUID, output name) pairs for the
            result of an earlier action.
    """
    text, attachments = "", {}
    for part in parts:
        if isinstance(part, str):
            text += part
        else:
            attachments[f"{{{len(text)}, 1}}"] = {"OutputUUID": part[0], "Type": "ActionOutput",
                                                  "OutputName": part[1]}
            text += _OBJECT
    return {"Value": {"string": text, "attachmentsByRange": attachments},
            "WFSerializationType": "WFTextTokenString"}


def build(folder: str, language: str) -> bytes:
    """The unsigned command, as a binary plist.

    Args:
        folder: the folder inside the Shortcuts folder of iCloud Drive where
            the inbox file is written ("Spendif.ai").
        language: the interface language code; it sets the dictation
            language and the words of the question.
    """
    def new_id() -> str:
        return str(uuid.uuid4()).upper()

    dictated, menu, menu_end, date, line = new_id(), new_id(), new_id(), new_id(), new_id()
    question, cash_word, card_word = _ASK.get(language, _ASK["en"])

    def menu_item(title: str, written: str) -> list[dict]:
        return [{"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                 "WFWorkflowActionParameters": {"GroupingIdentifier": menu, "WFControlFlowMode": 1,
                                                "WFMenuItemTitle": title}},
                {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                 "WFWorkflowActionParameters": {"UUID": new_id(), "WFTextActionText": written}}]

    actions = [
        {"WFWorkflowActionIdentifier": "is.workflow.actions.dictatetext",
         "WFWorkflowActionParameters": {"UUID": dictated, "WFDictateTextStopListening": "After Pause",
                                        "WFSpeechLanguage": _SPEECH.get(language, "en-US")}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
         "WFWorkflowActionParameters": {"GroupingIdentifier": menu, "WFControlFlowMode": 0,
                                        "WFMenuPrompt": question, "WFMenuItems": [cash_word, card_word]}},
        *menu_item(cash_word, "contanti"),
        *menu_item(card_word, "carta"),
        {"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
         "WFWorkflowActionParameters": {"GroupingIdentifier": menu, "WFControlFlowMode": 2, "UUID": menu_end}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.format.date",
         "WFWorkflowActionParameters": {
             "UUID": date, "WFDateFormatStyle": "Custom", "WFDateFormat": "yyyy-MM-dd HH:mm",
             "WFDate": {"Value": {"string": _OBJECT, "attachmentsByRange": {"{0, 1}": {"Type": "CurrentDate"}}},
                        "WFSerializationType": "WFTextTokenString"}}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
         "WFWorkflowActionParameters": {"UUID": line, "WFTextActionText": _token([
             (date, "Formatted Date"), " | ", (menu_end, "Menu Result"), " | ", (dictated, "Dictated Text"), "\n"])}},
        {"WFWorkflowActionIdentifier": "is.workflow.actions.file.append",
         "WFWorkflowActionParameters": {"WFFilePath": f"{folder}/{INBOX_NAME}", "WFAppendFileWriteMode": "Append",
                                        "WFInput": _token([(line, "Text")])}},
    ]
    return plistlib.dumps({
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2607.0.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": 59511, "WFWorkflowIconStartColor": 463140863},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowTypes": [],
        "WFWorkflowHasShortcutInputVariables": False,
    }, fmt=plistlib.FMT_BINARY)
