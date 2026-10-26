"""Date, amount and category of an expense from the user's own words (spec 32).

Everything here is deterministic and pure: text in, values out, `today`
passed in. The amount is read as people say it, not as they write it
("23 e 40" is 23,40; "23 e 5" is 23,05: cents said are cents, spec 32, 4.1).
Italian words; digits work in any language.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

# ── Text ─────────────────────────────────────────────────────────────────────


def normalize(text: str) -> str:
    """Lower case, accents and apostrophes gone, the euro sign a word."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("€", " euro ")
    return re.sub("['`" + chr(0x2019) + "]", " ", text)     # typed or autocorrected apostrophe


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


# ── Numbers said with words ──────────────────────────────────────────────────

_UNITS = {"zero": 0, "uno": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
          "sei": 6, "sette": 7, "otto": 8, "nove": 9}
_TEENS = {"dieci": 10, "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14,
          "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18, "diciannove": 19}
_TENS = {"venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50, "sessanta": 60,
         "settanta": 70, "ottanta": 80, "novanta": 90}


def _below_100(w: str) -> int | None:
    if w in _UNITS:
        return _UNITS[w]
    if w in _TEENS:
        return _TEENS[w]
    for name, value in _TENS.items():
        if w == name:
            return value
        # "ventitre", and "ventuno"/"ventotto" with the vowel dropped
        for head in (name, name[:-1]):
            rest = w[len(head):]
            if w.startswith(head) and rest in _UNITS and rest != "zero":
                if head == name or rest[0] in "uo":
                    return value + _UNITS[rest]
    return None


def _below_1000(w: str) -> int | None:
    if not w:
        return None
    i = w.find("cento")
    if i < 0:
        return _below_100(w)
    head, rest = w[:i], w[i + 5:]
    hundreds = 1 if head == "" else _UNITS.get(head)
    if hundreds is None or hundreds == 0 or head == "uno":
        return None
    if not rest:
        return hundreds * 100
    tail = _below_100(rest)
    if tail is None:
        tail = _below_100("o" + rest)        # "centotto", "centottanta"
    return None if tail is None else hundreds * 100 + tail


def words_to_int(word: str) -> int | None:
    """An Italian number written as one word ("ventitre", "milleduecento"),
    or None when the word is not a number."""
    w = normalize(word).strip()
    if not w.isalpha():
        return None
    if w.startswith("mille"):
        rest = w[5:]
        if not rest:
            return 1000
        tail = _below_1000(rest)
        return None if tail is None else 1000 + tail
    if "mila" in w:
        head, rest = w.split("mila", 1)
        thousands = _below_1000(head)
        if thousands is None or thousands < 2:
            return None
        tail = _below_1000(rest) if rest else 0
        return None if tail is None else thousands * 1000 + tail
    return _below_1000(w)


# ── Amount ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Amount:
    value: Decimal          # positive: an expense said as a number
    cents_said: bool        # False for "23": maybe 23,00, maybe 23,40 said in a hurry


_DECIMAL = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+[.,]\d{1,2}(?!\d)")
_TOKEN = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{1,2}|\d+[.,]\d{1,2}(?!\d)|\d+|[a-z]+")
_CENT_WORDS = {"centesimi", "centesimo", "cent"}
_EURO_WORDS = {"euro", "eur", "euri"}


def _written_decimal(tok: str) -> Decimal | None:
    if not _DECIMAL.fullmatch(tok):
        return None
    tok = tok.replace(".", "") if "," in tok and "." in tok else tok
    try:
        return Decimal(tok.replace(",", "."))
    except InvalidOperation:
        return None


def _number(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    return words_to_int(tok)


def _read_amount(tokens: list[str]) -> tuple[Amount | None, set[int]]:
    """The amount in a list of tokens, and the positions it used."""
    for i, tok in enumerate(tokens):
        dec = _written_decimal(tok)
        if dec is not None:
            used = {i}
            if i + 1 < len(tokens) and tokens[i + 1] in _EURO_WORDS:
                used.add(i + 1)
            return (Amount(dec, True) if dec > 0 else None), used
        n = _number(tok)
        if n is None:
            continue
        # "zero e 90" is 0,90; a zero alone is no amount
        if n == 0 and (tokens[i + 1] if i + 1 < len(tokens) else "") not in (
                "e", "virgola", "punto", *_EURO_WORDS):
            continue
        used = {i}
        j = i + 1
        nxt = tokens[j] if j < len(tokens) else ""
        if nxt in _CENT_WORDS and n < 100:
            return Amount(Decimal(n) / 100, True), used | {j}
        euro = nxt in _EURO_WORDS
        if euro:
            used.add(j)
            j += 1
        conn = tokens[j] if j < len(tokens) else ""
        k = j + 1 if conn in ("e", "virgola", "punto") else (j if euro else None)
        if k is not None and k < len(tokens):
            cents_tok = tokens[k]
            m = _number(cents_tok)
            if m is not None and m < 100:
                if conn in ("virgola", "punto"):
                    # a written-style decimal said aloud: ",4" is 40 cents
                    cents = (Decimal("0." + cents_tok) if cents_tok.isdigit()
                             else Decimal(m) / (10 if m < 10 else 100))
                else:
                    cents = Decimal(m) / 100
                used |= set(range(j, k + 1))
                if k + 1 < len(tokens) and tokens[k + 1] in _CENT_WORDS:
                    used.add(k + 1)
                if n + cents > 0:
                    return Amount(Decimal(n) + cents, True), used
        if n == 0:
            continue
        return Amount(Decimal(n), False), used
    return None, set()


def parse_amount(text: str) -> Amount | None:
    """The amount in *text*, as said or dictated (spec 32, 4.1)."""
    amount, _ = _read_amount(_TOKEN.findall(normalize(text)))
    return amount


# ── Date ─────────────────────────────────────────────────────────────────────

_MONTHS = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
           "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11,
           "dicembre": 12, "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
           "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12}
_WEEKDAYS = {"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4,
             "sabato": 5, "domenica": 6}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_RE = r"(\d{1,2}|primo|[a-z]+)"

_D_NUMERIC = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b|\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")
_D_MONTH = re.compile(rf"\b(?:(?:il|del|al|lo|l)\s+)?{_DAY_RE}\s+(?:di\s+)?({_MONTH_RE})\b(?:\s+(\d{{4}}))?")
# Today and yesterday also in the other languages of the interface: the page
# shows its example in the user's language.
_TODAY_WORDS = ("oggi", "stamattina", "stamani", "stasera", "today", "aujourd hui", "heute", "hoy")
_YESTERDAY_WORDS = ("ieri", "yesterday", "hier", "gestern", "ayer")
_D_RELATIVE = re.compile(r"\b(l\s+altro\s+ieri|altro\s*ieri|avantieri|ieri\s+l\s+altro|"
                         + "|".join(_YESTERDAY_WORDS + _TODAY_WORDS) + r")\b")
_D_WEEKDAY = re.compile(rf"\b({'|'.join(_WEEKDAYS)})(\s+scors[oa])?\b")
_D_DAY_ONLY = re.compile(rf"\b(?:il|del|al|lo|l)\s+{_DAY_RE}\b")


def _day_number(tok: str) -> int | None:
    n = 1 if tok == "primo" else _number(tok)
    return n if n is not None and 1 <= n <= 31 else None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _past(day: date | None, today: date) -> date | None:
    """A date said without the year is the last one not yet to come."""
    if day is None or day <= today:
        return day
    return _safe_date(day.year - 1, day.month, day.day)


def _find_date(text: str, today: date) -> tuple[date | None, tuple[int, int] | None]:
    """The date in normalized *text* and the span it took."""
    for m in _D_NUMERIC.finditer(text):
        d, mo, y = (m.group(1), m.group(2), m.group(3)) if m.group(1) else m.group(4, 5, 6)
        if y:
            year = int(y) + (2000 if len(y) == 2 else 0)
            day = _safe_date(year, int(mo), int(d))
        else:
            day = _past(_safe_date(today.year, int(mo), int(d)), today)
        if day:
            return day, m.span()
    for m in _D_MONTH.finditer(text):
        n = _day_number(m.group(1))
        if n is None:
            continue
        month = _MONTHS[m.group(2)]
        if m.group(3):
            day = _safe_date(int(m.group(3)), month, n)
        else:
            day = _past(_safe_date(today.year, month, n), today)
        if day:
            return day, m.span()
    m = _D_RELATIVE.search(text)
    if m:
        word = m.group(1)
        back = 0 if word in _TODAY_WORDS else 1 if word in _YESTERDAY_WORDS else 2
        return today - timedelta(days=back), m.span()
    m = _D_WEEKDAY.search(text)
    if m:
        back = (today.weekday() - _WEEKDAYS[m.group(1)]) % 7
        if back == 0 and m.group(2):
            back = 7            # "domenica scorsa" said on a Sunday
        return today - timedelta(days=back), m.span()
    for m in _D_DAY_ONLY.finditer(text):
        n = _day_number(m.group(1))
        if n is None:
            continue
        # "il 12": this month if it has come, else the last month that has it
        y, mo = today.year, today.month
        for _ in range(3):
            if n <= calendar.monthrange(y, mo)[1] and date(y, mo, n) <= today:
                return date(y, mo, n), m.span()
            y, mo = (y - 1, 12) if mo == 1 else (y, mo - 1)
    return None, None


def parse_date(text: str, today: date) -> date | None:
    """The date in *text*: relative ("ieri", "sabato scorso", "il 12") or
    explicit ("12 maggio", "12/05/2026")."""
    return _find_date(normalize(text), today)[0]


# ── Both in one sentence ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Parsed:
    day: date | None
    amount: Decimal | None
    cents_said: bool
    sure: bool          # nothing left over: no need to repeat what was understood


def parse_expense(text: str, today: date) -> Parsed:
    """Date and amount from one sentence: the date first, the amount in what
    is left. Numbers nobody used make the reading unsure (spec 32, 4.1: the
    bot repeats the values only when it is not sure)."""
    norm = normalize(text)
    day, span = _find_date(norm, today)
    if span:
        norm = norm[:span[0]] + " " + norm[span[1]:]
    tokens = _TOKEN.findall(norm)
    amount, used = _read_amount(tokens)
    leftover = [t for i, t in enumerate(tokens)
                if i not in used and (t[0].isdigit() or _number(t) is not None)]
    return Parsed(day=day, amount=amount.value if amount else None,
                  cents_said=amount.cents_said if amount else False,
                  sure=not leftover)


# ── Answers ──────────────────────────────────────────────────────────────────

# A yes is said in many ways (spec 32, 4.2 A).
_YES = {"si", "yes", "oui", "ja", "ok", "okay", "esatto", "esatta", "giusto", "giusta", "certo",
        "perfetto", "confermo", "confermato", "confermata", "conferma", "esattamente", "proprio",
        "corretto", "corretta", "bene", "vai", "procedi", "affermativo", "certamente", "sicuro",
        "assolutamente", "ovvio", "yep", "yeah", "sure", "right", "correct", "exactly",
        "genau", "richtig", "claro", "vale", "exacto", "correcto", "dacord", "vabbe", "vabbene"}
_LEAD = {"allora", "beh", "be", "ah", "eh", "oh", "ehm", "mh", "uhm"}     # fillers before the answer
_IT_IS = {"questa", "quella", "lei", "lui", "questo", "quello", "giusta", "giusto"}
_NO = {"no", "non", "nein", "nessuno", "nessuna", "niente", "sbagliato"}
_BUT = {"ma", "pero", "categoria", "altra"}


# Closing the session (spec 32, 4.6). Explicit closings close at any step;
# the bare refusals only while the bot waits for a new expense, since halfway
# they answer the question asked ("no, not this one").
_CLOSE_WORDS = {"basta", "fine", "finito", "finita", "chiudi", "chiudiamo", "stop", "tutto",
                "done", "finished", "enough", "fini", "termine", "fertig", "genug", "schluss",
                "listo", "terminado", "acabado"}
_CLOSE_PHRASES = ("ho finito", "abbiamo finito", "non ho altre", "nient altro", "nessun altra",
                  "per oggi basta", "lascia stare", "lasciamo stare", "e tutto", "per ora basta",
                  "that s all", "that s it", "no more", "c est tout", "j ai fini", "das war s",
                  "das ist alles", "ich bin fertig", "eso es todo", "he terminado", "ya esta")
_WAITING_CLOSE = {"no", "niente", "nessuna", "nessuno", "nope", "non", "nein", "nada", "rien"}
_FILLER = {"ok", "okay", "si", "va", "bene", "allora", "grazie", "per", "ora", "oggi", "e", "cosi",
           "thanks", "merci", "danke", "gracias", "yes", "oui", "ja"}


# Correcting the last expense confirmed (spec 32, 4.7).
_CORRECT_WORDS = {"correggi", "correggere", "correzione", "sbagliato", "sbagliata", "aspetta",
                  "cambia", "cambiare", "modifica", "wrong", "wait", "change", "fix", "falsch",
                  "warte", "andern", "corrige", "attends", "erreur", "cambiar", "espera", "corrige",
                  "equivocado", "equivocada"}


def says_an_expense(text: str) -> bool:
    """True when *text* names an amount or a date. "Per oggi" is when to
    stop, not the day of an expense, unless an amount comes with it."""
    if parse_amount(text) is not None:
        return True
    undated = " ".join(w for w in _words(text) if w not in _TODAY_WORDS)
    return parse_date(undated, date.today()) is not None


def is_correction(text: str) -> bool:
    """True when *text* asks to correct the last expense confirmed: never
    when it says a date or an amount, which make it a new expense."""
    words = _words(text)
    if not words or not set(words) & _CORRECT_WORDS:
        return False
    undated = " ".join(w for w in words if w not in _TODAY_WORDS)
    return parse_amount(text) is None and parse_date(undated, date.today()) is None


def is_closing(text: str, waiting: bool) -> bool:
    """True when *text* closes the session: never when it says a date or an
    amount ("basta, ieri 20 euro" is an expense); a bare "no" only when
    *waiting* for a new expense."""
    flat = " ".join(_words(text))
    # "per oggi basta": today here is when to stop, not the day of an expense.
    undated = " ".join(w for w in flat.split() if w not in _TODAY_WORDS)
    if parse_amount(text) is not None or parse_date(undated, date.today()) is not None:
        return False
    if not flat:
        return False
    if any(p in f" {flat} " for p in (f" {x} " for x in _CLOSE_PHRASES)):
        return True
    words = [w for w in flat.split() if w not in _FILLER]
    if not words:
        return False
    if all(w in _CLOSE_WORDS for w in words):
        return True
    return waiting and all(w in _WAITING_CLOSE | _CLOSE_WORDS for w in words)


def yes_no(text: str) -> str | None:
    """"yes", "yes_other" (yes, but the category is another), "no" or None."""
    words = _words(text)
    while words and words[0] in _LEAD:
        words = words[1:]
    if not words:
        return None
    if (words[0] in _YES or words[:2] in (["va", "bene"], ["d", "accordo"], ["va", "be"])
            or (words[0] == "e" and len(words) > 1 and words[1] in _IT_IS)):
        return "yes_other" if _BUT & set(words) else "yes"
    if words[0] in _NO:
        return "no"
    return None


_ORDINALS = {"primo": 0, "prima": 0, "secondo": 1, "seconda": 1, "terzo": 2, "terza": 2,
             "quarto": 3, "quarta": 3, "quinto": 4, "quinta": 4, "sesto": 5, "sesta": 5}


def _number_word(w: str) -> int | None:
    """"2" or "due": the ears of the app write numbers as words."""
    return int(w) if w.isdigit() else _UNITS.get(w)


def _index_said(words: list[str], n: int) -> int | None:
    """"2", "due", "la seconda", "l'ultima": a position in a list of *n*."""
    for w in words:
        if w in _ORDINALS and _ORDINALS[w] < n:
            return _ORDINALS[w]
        if w in ("ultimo", "ultima"):
            return n - 1
    nums = [v for v in map(_number_word, words) if v is not None]
    if len(nums) == 1 and 1 <= nums[0] <= n:
        return nums[0] - 1
    return None


# ── Choosing ─────────────────────────────────────────────────────────────────

_STOP = {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "da", "in", "con", "su",
         "per", "tra", "fra", "a", "al", "allo", "alla", "ai", "agli", "alle", "del", "dello",
         "della", "dei", "degli", "delle", "dal", "dalla", "nel", "nella", "sul", "sulla", "e",
         "o", "ed", "che", "quella", "quello", "questa", "questo", "mi", "ho", "ha", "era",
         "categoria", "l", "d"}


def _content(text: str) -> list[str]:
    return [w for w in _words(text) if w not in _STOP and not w.isdigit()]


def _stem(w: str) -> str:
    """Singular and plural, masculine and feminine together: "farmaco", "farmaci"."""
    return w[:-1] if len(w) >= 4 and w[-1] in "aeio" else w


def pick_movement(text: str, candidates: list[tuple[str, date]], today: date) -> int | None:
    """Which of the movements found the user means (spec 32, 4.2 B): by
    position, by counterpart or by date. None when it is not one of them."""
    words = _words(text)
    n = len(candidates)
    for w in words:
        if w in _ORDINALS or w in ("ultimo", "ultima"):
            return _index_said(words, n)
    # a number alone ("uno", "il 2") is a position, unless a movement shown
    # falls on that day: then "il 13" is the 13th
    said = [w for w in words if w not in ("il", "l", "la", "numero")]
    if len(said) == 1 and _number_word(said[0]) is not None:
        if all(d.day != _number_word(said[0]) for _, d in candidates):
            return _index_said(said, n)
    content = {_stem(w) for w in _content(text) if len(w) >= 3}
    if content:
        named = [i for i, (name, _) in enumerate(candidates)
                 if content & {_stem(w) for w in _words(name)}]
        if len(named) == 1:
            return named[0]
    day = parse_date(text, today)
    if day is not None:
        on_day = [i for i, (_, d) in enumerate(candidates) if d == day]
        return on_day[0] if len(on_day) == 1 else None
    return _index_said(words, n)


def label(pair: tuple[str, str]) -> str:
    """"Casa / Arredamento": the category as the app shows it."""
    cat, sub = pair
    return f"{cat} / {sub}" if sub else cat


def _pair_stems(pair: tuple[str, str]) -> set[str]:
    return {_stem(w) for w in _words(label(pair)) if w not in _STOP}


def category_match(text: str, pairs: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The one category the words name, or None (spec 32, 4.3 step 1).

    The words name a category when they are its label, or when every one of
    them is in the label of that category and of no other.
    """
    said = normalize(text).strip()
    for pair in pairs:
        if normalize(label(pair)).strip() == said or normalize(pair[1]).strip() == said:
            return pair
    words = {_stem(w) for w in _content(text)}
    if not words:
        return None
    hits = [pair for pair in pairs if words <= _pair_stems(pair)]
    return hits[0] if len(hits) == 1 else None


def _closeness(words: list[str], pair: tuple[str, str]) -> float:
    targets = [w for w in _words(label(pair)) if w not in _STOP]
    if not words or not targets:
        return 0.0
    total = 0.0
    for w in words:
        total += max(1.0 if _stem(w) == _stem(t) else SequenceMatcher(None, w, t).ratio()
                     for t in targets)
    return total / len(words)


def nearest_categories(text: str, pairs: list[tuple[str, str]], k: int = 3,
                       exclude: list[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """The *k* categories nearest to the words (spec 32, 4.3 step 2): word
    by word, a word of the label equal in its stem or close in its letters."""
    words = _content(text)
    said = normalize(text)
    scored = []
    for order, pair in enumerate(pairs):
        if pair in exclude:
            continue
        score = _closeness(words, pair) + 0.1 * SequenceMatcher(None, said, normalize(pair[1])).ratio()
        scored.append((-score, order, pair))
    return [pair for _, _, pair in sorted(scored)[:k]]


def pick_category(text: str, options: list[tuple[str, str]]) -> int | None:
    """Which of the categories shown the user chose: by position or by name."""
    index = _index_said(_words(text), len(options))
    if index is not None:
        return index
    pair = category_match(text, options)
    return options.index(pair) if pair is not None else None


# ── Only the new part of a dictation ─────────────────────────────────────────

def _after(text: str, sent: str) -> int | None:
    """Where *text* goes on after starting with *sent*, comparing letters and
    digits only; len(text) when *text* is *sent* or a part of its start; None
    when it does not start with it."""
    want = [c for c in sent.casefold() if c.isalnum()]
    if not want:
        return None
    k = 0
    for i, c in enumerate(text.casefold()):
        if not c.isalnum():
            continue
        if k == len(want):
            # a number or word that only starts like the one sent is new
            return i if not text[i - 1].isalnum() else None
        if c != want[k]:
            return None
        k += 1
    return len(text)


def new_part(text: str, sent: list[str]) -> str:
    """The part of *text* after the answers already sent (spec 32, 3.2): the
    Mac's dictation writes again everything it heard since it was turned on,
    then the new words. The longest run of the last answers wins; spaces,
    commas and the euro sign do not count. Empty when nothing is new."""
    for j in range(len(sent)):
        cut = _after(text, " ".join(sent[j:]))
        if cut is not None:
            return re.sub(r"^\W+", "", text[cut:])
    return text
