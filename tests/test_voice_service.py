"""The app's own ears and voice (EXPERIMENTAL, spec 32, 3.2): the speech gate,
the audio conversions and the prompt; fake models, no weights needed."""
from __future__ import annotations

import numpy as np

from services.voice_service import (VoiceService, has_speech, pcm_to_wav, speakable,
                                    wav_to_pcm)

RATE = 16000


def _tone(seconds, amp=0.3, rate=RATE):
    """A word-like burst: it rises and falls, as a voice does."""
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.hanning(len(t)) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _room(seconds, amp=0.003, seed=0):
    return (np.random.default_rng(seed).standard_normal(int(seconds * RATE)) * amp).astype(np.float32)


class _Ears:
    def __init__(self):
        self.calls = []

    def transcribe(self, pcm, prompt):
        self.calls.append(prompt)
        return "ieri 23 e 40"


def test_silence_and_steady_noise_are_not_speech():
    assert not has_speech(np.zeros(RATE * 2, np.float32))
    assert not has_speech(_room(2, amp=0.02))
    assert not has_speech(_room(2, amp=0.06))


def test_a_voice_is_speech_even_a_short_one():
    # push-to-talk: some room before and after
    assert has_speech(np.concatenate([_room(0.4), _tone(1.0), _room(0.4)]))
    # a "si'" of a third of a second with no room around it
    assert has_speech(_tone(0.3))


def test_wav_round_trip_and_resample():
    pcm = _tone(0.5, rate=48000)
    back = wav_to_pcm(pcm_to_wav(pcm, 48000))
    assert abs(len(back) - RATE // 2) <= 1


def test_no_speech_never_reaches_the_model():
    ears = _Ears()
    heard = VoiceService(stt=ears).hear(pcm_to_wav(np.zeros(RATE, np.float32), RATE))
    assert (heard.text, heard.speech, ears.calls) == ("", False, [])


def test_names_on_screen_go_into_the_prompt_never_a_yes():
    ears = _Ears()
    wav = pcm_to_wav(np.concatenate([_room(0.3), _tone(0.8), _room(0.3)]), RATE)
    heard = VoiceService(stt=ears).hear(wav, ["Esselunga", "Bar Centrale", "Esselunga"])
    assert heard.text == "ieri 23 e 40"
    prompt = ears.calls[0]
    assert prompt.count("Esselunga") == 1 and "Bar Centrale" in prompt
    words = prompt.lower().replace(".", " ").replace(",", " ").split()
    assert "si" not in words and "no" not in words


def test_a_category_path_is_said_with_a_pause():
    assert speakable("Casa / Manutenzione, va bene?") == "Casa, Manutenzione, va bene?"


def test_the_silence_around_the_answer_never_reaches_the_model():
    """A long recording with one short answer in it: the model hears the
    answer and a little room, not the silence that makes it invent words."""
    lengths = []

    class _Measure(_Ears):
        def transcribe(self, pcm, prompt):
            lengths.append(len(pcm) / RATE)
            return super().transcribe(pcm, prompt)

    clip = np.concatenate([_room(1.0), _tone(1.0), _room(37.0)])
    VoiceService(stt=_Measure()).hear(pcm_to_wav(clip, RATE))
    assert 1.0 <= lengths[0] <= 2.0


def test_trimming_keeps_a_whole_short_answer():
    from services.voice_service import trim_silence
    clip = np.concatenate([_room(2.0), _tone(0.3), _room(2.0)])
    kept = trim_silence(clip)
    assert 0.3 <= len(kept) / RATE <= 1.0


def test_a_reply_starts_with_half_a_second_of_silence():
    """Headphones switching back from the microphone eat the first moment of
    sound: the reply starts with silence, so they eat no words."""
    class _Voice:
        def synthesize(self, text):
            return np.full(22050, 0.5, np.float32), 22050
    pcm = wav_to_pcm(VoiceService(tts=_Voice()).say("Ciao"))
    assert np.abs(pcm[: RATE * 4 // 10]).max() < 1e-3
    assert np.abs(pcm[RATE * 6 // 10:]).max() > 0.1
