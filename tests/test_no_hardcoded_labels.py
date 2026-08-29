"""Gate: nessuna etichetta scritta a mano nei widget della UI.

Il debito si riforma da solo. Questo test fallisce quando una nuova stringa
visibile all'utente entra nel codice invece che nei file di traduzione, ed e'
il motivo per cui AI-287 non torna a 135 occorrenze fra sei mesi.

ESENZIONE: ui/debugger_page.py. E' visibile solo con SPENDIFAI_DEV_MODE=1 e
non raggiunge mai un utente finale; tradurla costerebbe il 43% del lavoro
totale per zero beneficio. La scelta e' esplicita, non una dimenticanza.
"""
import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
ESENTI = {"debugger_page.py"}

_WIDGET = (
    "button|caption|write|markdown|subheader|header|title|info|success|warning|error|"
    "checkbox|radio|selectbox|text_input|number_input|multiselect|toggle|expander|metric|"
    "tab|form_submit_button|download_button|slider|date_input|file_uploader|toast"
)
PAT = re.compile(rf'st\.(?:{_WIDGET})\s*\(\s*(f?)"([^"]{{3,}})"', re.S)
PAT_COL = re.compile(
    r'\b(?:pc\d|[a-z_]+col\d?|c\d)\.(?:button|caption|markdown|write|metric|download_button)'
    r'\s*\(\s*f?"([^"]{3,})"', re.S
)
_INTERPOLAZIONE = re.compile(r"\{[^}]*\}")
_ENTITA_HTML = re.compile(r"&[a-z]+;")


def _e_etichetta(s: str) -> bool:
    """Vero se la stringa contiene testo umano da tradurre.

    Esclude: f-string che avvolgono gia' una t(...), entita' HTML, e stringhe
    che fuori dalle graffe non hanno abbastanza lettere per essere una frase.
    """
    if "t(" in s or "t_fn(" in s:
        return False
    testo = _ENTITA_HTML.sub("", _INTERPOLAZIONE.sub("", s))
    return len(re.sub(r"[^A-Za-zÀ-ÿ]", "", testo)) >= 4


def test_no_hardcoded_labels_in_ui():
    trovate: list[str] = []
    for f in sorted(UI_DIR.rglob("*.py")):
        if f.name in ESENTI:
            continue
        txt = f.read_text(encoding="utf-8")
        for m in list(PAT.finditer(txt)) + list(PAT_COL.finditer(txt)):
            s = m.groups()[-1]
            if s.startswith(("http", "_")) or not _e_etichetta(s):
                continue
            riga = txt[: m.start()].count("\n") + 1
            trovate.append(f"{f.relative_to(UI_DIR.parent)}:{riga}: {s[:60]!r}")
    assert not trovate, (
        "etichette hardcoded: vanno in ui/i18n/{it,en}.json e richiamate con t()\n  "
        + "\n  ".join(trovate)
    )
