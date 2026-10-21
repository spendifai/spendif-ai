"""Payment intermediaries: the counterpart is the merchant after the asterisk.

Spec: documents/04_software_engineering/20_intermediari_di_pagamento.md.

PayPal, SumUp and the like are transparent: the money goes on to the
merchant. Card statements write them as ``PAYPAL *TRENORD SRL 0272494949``.
A prefix is an intermediary when it is in the table (seed, learned, added by
the user); the table learns a prefix followed by at least two different
merchant names. Pure functions: no database, no interface, no model.
"""
from __future__ import annotations

import re
from collections import defaultdict

from core.normalizer import tidy_text
from core.rule_key import words

# "PREFIX *REST" on the first line; the prefix is short and has no asterisk.
_SPLIT = re.compile(r"^\s*([^*\n]{1,30}?)\s*\*\s*(\S[^\n]*)")
# Where the merchant name ends: two or more spaces, a run of 5+ digits, a
# new line, another asterisk.
_MERCHANT_END = re.compile(r"\s{2,}|\d{5,}|\n|\*")
# What separates an intermediary from the merchant's field: an asterisk, or
# two or more spaces. A single space ("PayPal Europe S.a.r.") is the
# intermediary's own name: it is then the counterpart itself.
_FIELD_SEPARATOR = re.compile(r"[ \t]*\*[ \t]*|[ \t]{2,}")

# The merchant field of a card payment is at most 22-25 characters: a
# description without gaps of spaces must not drag the rest of the line in.
_MERCHANT_MAX = 25
# A merchant has 3 letters in a row: "FATTURA24", "HRB" yes, "P33C6E454D" no.
_HAS_NAME = re.compile(r"[^\W\d_]{3,}")

MIN_NAMES = 2          # different merchant names that make a prefix an intermediary
_NAME_LETTERS = 4      # a merchant name has a word of 4+ letters and no digit


def prefix_key(text: str | None) -> str:
    """A prefix in reduced words: "PayPal " and "PAYPAL" are the same."""
    return " ".join(words(text))


def split(raw: str | None) -> tuple[str, str] | None:
    """(prefix in reduced words, the rest) of "PREFIX *REST", or None."""
    m = _SPLIT.match(raw or "")
    if not m:
        return None
    prefix = prefix_key(m.group(1))
    return (prefix, m.group(2)) if prefix else None


def _is_name(word: str) -> bool:
    return sum(c.isalpha() for c in word) >= _NAME_LETTERS and not any(c.isdigit() for c in word)


def _prefix_regex(prefix: str) -> re.Pattern:
    """The prefix as whole words anywhere in a text, any case, any separator."""
    body = r"[\W_]+".join(re.escape(w) for w in prefix.split())
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.IGNORECASE)


def find(raw: str | None, active: set[str]) -> tuple[str, str] | None:
    """(intermediary, merchant) in a raw description, or None.

    The intermediary is searched anywhere, as whole words, not only at the
    start and not only before an asterisk (spec 20, founder 2026-10-20):
    Amex "PAYPAL *TRENORD SRL ...", Nexi "ALTRE SPESE PAYPAL *FATTURA24",
    Mediobanca "... A (LUX) PAYPAL  GONEL.SILVIA   CARTA ***7746". The
    merchant is the field that follows an asterisk or two or more spaces, up
    to the next gap of spaces, a run of 5+ digits, a new line or an asterisk;
    after a single space there is no merchant field ("Addebito SDD - PayPal
    Europe S.a.r.": PayPal itself is the counterpart). A merchant without a
    word of 3 letters in a row ("X39 34987844", "P33C6E454D") is no merchant,
    and the field stops at 25 characters, the length of a card merchant name.
    """
    text = raw or ""
    for prefix in sorted(active, key=lambda p: -len(p)):
        for m in _prefix_regex(prefix).finditer(text):
            sep = _FIELD_SEPARATOR.match(text, m.end())
            if sep is None or sep.end() == m.end():
                continue
            field = _MERCHANT_END.split(text[sep.end():], maxsplit=1)[0]
            if len(field) > _MERCHANT_MAX:
                cut = field[:_MERCHANT_MAX + 1]
                field = cut[:cut.rfind(" ")] if " " in cut else cut[:_MERCHANT_MAX]
            merchant = tidy_text(field)
            if _HAS_NAME.search(merchant):
                return prefix, merchant
    return None


def merchant_after(raw: str | None, active: set[str]) -> str | None:
    """The merchant of a movement paid through an active intermediary, or
    None; already tidied (spec 21: "GONEL.SILVIA" -> "GONEL SILVIA")."""
    hit = find(raw, active)
    return hit[1] if hit else None


def learn(texts, min_names: int = MIN_NAMES) -> dict[str, set[str]]:
    """{prefix: merchant names} for the prefixes followed by min_names+ names.

    *texts*: any strings (raw descriptions, cells of a file); only those in
    the "PREFIX *REST" form count. A name is the first word after the
    asterisk with 4+ letters and no digit, so Amazon's order codes are not
    names and GOOGLE*CLOUD, always CLOUD, stays a merchant.
    """
    names: dict[str, set[str]] = defaultdict(set)
    for text in texts:
        if not isinstance(text, str) or "*" not in text:
            continue
        parts = split(text)
        if parts is None:
            continue
        after = words(parts[1])
        if after and _is_name(after[0]):
            names[parts[0]].add(after[0])
    return {p: n for p, n in names.items() if len(n) >= min_names}


def is_dirty(description: str | None, raw: str | None, active: set[str]) -> str | None:
    """The merchant to write when a saved description still shows the intermediary.

    Dirty: the raw description holds an active intermediary with a merchant
    field (find) and the description is the intermediary or starts with it
    ("PAYPAL", "PAYPAL *EASYPARK", "PAYPAL GONEL.SILVIA"). A description
    changed by the user does not look like that and is left alone.
    """
    hit = find(raw, active)
    if hit is None:
        return None
    prefix, merchant = hit
    desc = prefix_key(description)
    if desc == prefix or desc.startswith(prefix + " "):
        return merchant
    return None
