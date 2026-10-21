"""Rules written by the product: the merchant name as the text of a rule.

Spec: documents/04_software_engineering/18_regole_per_chiave.md.

A tick such as "always like this" means "this merchant always has this
category", not "this exact string". The product turns it into a rule that
"starts with" the merchant name, in the fields a rule already has. The name
(the key) comes from the merchant list (NSI) when it knows the merchant in
the user's country, otherwise from the model; either way it passes the same
deterministic seal, and when it does not the rule is the exact description,
as before.

Everything here is pure: no database, no interface, no model call.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from core.initials import drop_apostrophes, join_initials
from core.nsi_lookup import nsi_lookup as _default_nsi

STARTS_WITH = "starts_with"

# A key needs one word with at least this many letters: "F24" or "IP" alone
# would start too many descriptions.
MIN_KEY_LETTERS = 4

KEY_KNOWN = "known"      # the rule text is a merchant the list knows
KEY_CHECK = "check"      # written by the model, or by hand: worth a look


def words(text: str | None) -> list[str]:
    """The words a rule and a description are compared on.

    Upper case, accents dropped, every character that is not a letter or a
    digit is a space, initials written apart joined (S.R.L. -> SRL): the
    same rule as the final description (core.normalizer.tidy_text), so the
    two always meet.
    """
    s = drop_apostrophes(text or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return join_initials(" ".join(s.split())).split()


def exact_key(text: str | None) -> str:
    """What "exact match" compares: case, accents and punctuation do not
    count, letters of every alphabet do (a Japanese description is not
    empty here, as it is for words())."""
    s = unicodedata.normalize("NFKD", drop_apostrophes(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return join_initials(" ".join(re.sub(r"[\W_]+", " ", s).split()))


def same_text(a: str | None, b: str | None) -> bool:
    """Exact match between a description and a rule's text (never on empty)."""
    ka = exact_key(a)
    return bool(ka) and ka == exact_key(b)


def starts_with(description: str | None, pattern: str | None) -> bool:
    """True when the description begins with the rule's words, whole words only."""
    p = words(pattern)
    return bool(p) and words(description)[: len(p)] == p


def seal(description: str, proposed: str | None) -> Optional[str]:
    """The text of a "starts with" rule for *description*, or None.

    The proposed key may only drop words of the description, never add,
    rewrite or reorder them, and needs a word of at least four letters.
    The rule text runs from the start of the description to the last word
    of the key, so the rule always takes the description it was born from
    ("AL CANTINONE VIA AGN" with key "CANTINONE" gives "AL CANTINONE").
    """
    d = words(description)
    k = words(proposed)
    if not k or not any(sum(c.isalpha() for c in w) >= MIN_KEY_LETTERS for w in k):
        return None
    j, last = 0, -1
    for i, w in enumerate(d):
        if j < len(k) and w == k[j]:
            j, last = j + 1, i
    if j < len(k):
        return None
    return " ".join(d[: last + 1])


def nsi_key(description: str, country: str | None, lookup=_default_nsi) -> Optional[str]:
    """The merchant the list knows in the user's country, sealed, or None.

    A brand of another country is no key here: the list holds brands that
    are common words elsewhere ("&pizza" in the United States).
    """
    match = lookup.lookup(description, user_country=country)
    if match is None or not match.brand:
        return None
    if match.countries and not (
        country and country.upper() in (c.upper() for c in match.countries)
    ):
        return None
    return seal(description, match.brand)


def key_confidence(pattern: str, match_type: str, country: str | None,
                   lookup=_default_nsi) -> Optional[str]:
    """How sure it is that a rule's text is the merchant's name.

    Worked out from the rule each time, never stored: KEY_KNOWN when a
    "starts with" rule is exactly a brand the list knows in the country,
    KEY_CHECK for any other "starts with" rule, None for the other types,
    whose text is no key.
    """
    if match_type != STARTS_WITH:
        return None
    known = nsi_key(pattern, country, lookup=lookup)
    return KEY_KNOWN if known is not None and words(known) == words(pattern) else KEY_CHECK
