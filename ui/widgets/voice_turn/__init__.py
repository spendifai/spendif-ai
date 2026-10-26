"""The app's own recorder (EXPERIMENTAL, spec 32, 3.2).

One microphone, opened once by a click and kept open for the whole
conversation, so the browser never has two captures at once (WebKit gives
sound to one only) and headphones do not switch mode at every turn. The
page decides the turns: it reads the reply aloud, then listens, keeping
half a second from before the first word, and ends the answer after a
pause. The recorded answer comes back as a 16 kHz WAV, in base64.
"""
from __future__ import annotations

import base64
from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component("voice_turn", path=str(Path(__file__).parent / "frontend"))


def turn_args(turn: int, wav: bytes | None, text: str | None, lang: str, labels: dict,
              last: bool = False) -> dict:
    """What the page needs for one turn: the reply as audio (our voice) or as
    text (the system voice), and the labels in the user's language. With
    *last* the reply closes the session: the recorder says it and turns the
    microphone off instead of listening (spec 32, 4.6)."""
    return {"turn": turn, "audio": base64.b64encode(wav).decode() if wav else "",
            "text": text or "", "lang": lang, "labels": labels, "last": bool(last)}


def answer_for(value, turn: int, taken: int) -> bytes | None:
    """The WAV of the answer to *turn*, once: None for an old turn, one
    already taken, or no answer yet."""
    if not isinstance(value, dict) or value.get("turn") != turn or turn == taken:
        return None
    wav = value.get("wav")
    return base64.b64decode(wav) if wav else None


def voice_turn(key: str, **args) -> None:
    """Draw the recorder; its answer is read from st.session_state[key]."""
    _component(key=key, default=None, **args)
