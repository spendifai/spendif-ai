"""What the product wrote when a tick became a rule (spec 18).

The page reruns after saving, so the note is kept in the session and shown
once at the top of the next run: the user sees the rule as it was written,
and how sure the product is that its text is the merchant's name.
"""
from __future__ import annotations

import streamlit as st

from services.rule_service import KEY_KNOWN, STARTS_WITH
from ui.i18n import t

_KEY = "_written_rules_note"
_SUMMARY = "_written_rules_summary"
_EXAMPLES = 3


def write_and_apply(rule_svc, tx_svc, description: str, pattern: str, match_type: str,
                    category: str, subcategory: str, origin: str,
                    apply: bool = True) -> tuple[int, str | None]:
    """Save the rule and apply it to the movements already there.

    A category chosen by hand on one movement is an exception to the rule,
    and a checked movement has its category confirmed: neither is
    overwritten. Returns (movements the rule was applied to,
    the note to show), and calls no Streamlit command: a click during a save
    stops the run at the next one, and must not stop it between two rules.
    """
    rule_svc.create_rule(pattern=pattern, match_type=match_type, category=category,
                         subcategory=subcategory, priority=10)
    txs = tx_svc.get_by_rule_pattern(pattern, match_type)
    targets = [tx for tx in txs if tx_svc.rule_may_change(tx)] if apply else []
    for tx in targets:
        tx_svc.apply_rule_category(tx.id, category, subcategory, origin=origin)
    if match_type == STARTS_WITH:
        note = _note_starts_with(rule_svc, description, pattern, [tx.description for tx in txs])
    elif description.casefold() in {d.casefold() for d in rule_svc.pending_rewrites()}:
        note = t("rules.written.queued", description=description)
    else:
        note = None
    return len(targets), note


def _note_starts_with(rule_svc, description: str, pattern: str, matched: list) -> str:
    others = sorted({d for d in matched if (d or "").upper() != description.upper()})
    conf = rule_svc.key_confidence(pattern, STARTS_WITH)
    note = t("rules.written.starts_with", pattern=pattern,
             confidence=t("rules.key.known" if conf == KEY_KNOWN else "rules.key.check"))
    if others:
        note += " " + t("rules.written.also", items=", ".join(others[:_EXAMPLES]))
    return note


def remember(notes: list[str], summary: str | None = None) -> None:
    """Keep what the save did for the next run: the page reruns after saving."""
    if summary:
        st.session_state[_SUMMARY] = summary
    if notes:
        st.session_state.setdefault(_KEY, []).extend(n for n in notes if n)


def rewrite_pending(rule_svc, tx_svc) -> None:
    """Rewrite the rules the model could not write at the tick (spec 18).

    Called where the model is free again: at the end of an import, and when
    the pages that ask about counterparts open, before they show anything.
    """
    notes = [
        _note_starts_with(rule_svc, description, pattern,
                          [tx.description for tx in tx_svc.get_by_rule_pattern(pattern, STARTS_WITH)])
        for description, pattern, _ in rule_svc.rewrite_pending()
    ]
    remember(notes)


def show_written_rules() -> None:
    """What the last save did, once: a toast the user sees wherever the page
    is scrolled to, and one line per rule written where it is called (under
    the save button in Controparti)."""
    summary = st.session_state.pop(_SUMMARY, None)
    if summary:
        st.toast(summary, icon="✅")
    for note in st.session_state.pop(_KEY, []):
        st.info(note)
