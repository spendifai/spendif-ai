"""Initials written apart: "S R L" is one word, "SRL".

Spec: documents/04_software_engineering/21_trattamento_testo.md
Founder's rule (2026-10-20): a run of two or more single letters separated
by single spaces is joined; a lone letter stays ("PAGAMENTO A ITA"). Applied
by every reduction of the text, so S.R.L., S R L and SRL always meet.
Also the apostrophe inside a word (drop_apostrophes). No imports on
purpose: the normalizer, the rule keys and the merchant list all use it.
"""
from __future__ import annotations

import re

# Two or more single letters, each a whole word, one space apart.
_RUN = re.compile(r"(?<!\S)[^\W\d_](?: [^\W\d_])+(?!\S)")


# An apostrophe inside a word is part of the name, not a separator: it is
# semantic, not syntactic (founder, 2026-10-21). McDonald's is one word.
_APOSTROPHE = re.compile(r"(?<=[^\W_])['\u2019](?=[^\W_])")


def drop_apostrophes(text: str) -> str:
    """For comparing: the apostrophes inside words go without splitting them,
    so McDonald's, MCDONALDS and McDonald\u2019s meet. The counterpart shown
    keeps them (core.normalizer.tidy_text)."""
    return _APOSTROPHE.sub("", text)


def join_initials(spaced: str) -> str:
    """Join the runs of single letters of a text whose words are one space
    apart: "CLIVATI 1969 S R L" -> "CLIVATI 1969 SRL", "A T M 47" -> "ATM 47"."""
    return _RUN.sub(lambda m: m.group(0).replace(" ", ""), spaced)
