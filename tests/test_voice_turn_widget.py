"""The app's own recorder (EXPERIMENTAL, spec 32, 3.2): one microphone open
for the whole conversation, turns decided by the app. The browser side is
tested by hand and with a browser; here the Python side of the exchange."""
from __future__ import annotations

import base64

from ui.widgets.voice_turn import answer_for, turn_args


def test_the_reply_travels_as_base64_and_an_empty_one_as_nothing():
    args = turn_args(4, wav=b"RIFFxyz", text="Ciao", lang="it-IT", labels={"start": "Inizia"})
    assert base64.b64decode(args["audio"]) == b"RIFFxyz"
    assert (args["turn"], args["text"], args["lang"], args["labels"]) == (4, "Ciao", "it-IT", {"start": "Inizia"})
    assert turn_args(5, wav=None, text=None, lang="it-IT", labels={})["audio"] == ""


def test_an_answer_counts_once_and_only_for_the_turn_it_answers():
    wav = base64.b64encode(b"RIFF").decode()
    assert answer_for({"turn": 3, "wav": wav}, turn=3, taken=2) == b"RIFF"
    assert answer_for({"turn": 3, "wav": wav}, turn=3, taken=3) is None      # already taken
    assert answer_for({"turn": 2, "wav": wav}, turn=3, taken=1) is None      # an old turn
    assert answer_for(None, turn=3, taken=2) is None
    assert answer_for({"turn": 3}, turn=3, taken=2) is None


def test_the_last_reply_turns_the_microphone_off():
    """Spec 32, 4.6: after the closing the recorder says it and stops."""
    assert turn_args(9, wav=None, text="Fine.", lang="it-IT", labels={}, last=True)["last"] is True
    assert turn_args(9, wav=None, text="Fatto.", lang="it-IT", labels={})["last"] is False
