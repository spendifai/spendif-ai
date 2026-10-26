"""VoiceService: the app's own ears and voice, all on this machine (spec 32, 3.2).

EXPERIMENTAL (branch exp/voice-round-trip). With the operating system's
dictation on, macOS holds speech synthesis, so a spoken back-and-forth is
impossible while the OS owns the audio. Here the app owns it: the page
records one answer (push-to-talk), this service turns it into text with a
local model, and turns the bot's reply into audio with a local voice.
Nothing leaves the machine.

- Ears: whisper.cpp (pywhispercpp, Metal on Apple Silicon, CPU elsewhere),
  model ggml-small-q5_1 (190 MB, MIT). A short prompt in the user's style
  ("23 e 40", "il 12 ottobre") steers the model to digits and "e"; the
  names on screen are added to it, so a counterpart is heard as written.
- A speech gate before the model: Whisper invents a sentence on silence or
  noise, and with the prompt it invents one of *ours* ("il 12 ottobre, 8
  euro", or a "si'" that would confirm a movement). No speech, no text.
- Voice: a Piper voice (it_IT-paola-medium, 63 MB) run by sherpa-onnx.
  Without it the page keeps the system voice of the browser.

The UI imports this service only; models load lazily, once per process.
"""
from __future__ import annotations

import io
import os
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from support.logging import setup_logging

logger = setup_logging()

SAMPLE_RATE = 16000

# The user's style, never a yes or a no: on a doubtful clip Whisper may
# return the prompt's own words, and a "si'" would confirm a movement.
_BASE_PROMPT = "Ieri, 23 e 40. Il 12 ottobre, 8 euro. Zero e 90. Alimentari."

# Speech gate (see has_speech): 20 ms frames; speech is louder than the
# clip's own floor by this much, for at least this long.
_FRAME = 320
_OVER_FLOOR_DB = 18.0
_MIN_LEVEL_DBFS = -50.0
_FLOOR_CAP_DBFS = -45.0     # a clip that is all voice has no floor of its own
_OVER_OWN_DB = 10.0         # and a voice rises and falls: steady noise does not
_MIN_SPEECH_MS = 160
_LEAD_IN_S = 0.5                 # silence before every reply
_MARGIN = SAMPLE_RATE * 3 // 10   # room kept around the words when trimming


def models_dir() -> Path:
    """Where the voice models live: SPENDIFAI_VOICE_MODELS, else the app's
    model folder."""
    env = os.environ.get("SPENDIFAI_VOICE_MODELS")
    return Path(env) if env else Path.home() / ".spendifai" / "models" / "voice"


# ── Audio helpers (pure) ──────────────────────────────────────────────────────


def wav_to_pcm(data: bytes) -> np.ndarray:
    """Mono float32 at 16 kHz from WAV bytes (what st.audio_input returns)."""
    with wave.open(io.BytesIO(data)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"16-bit WAV expected, got {8 * width}-bit")
    pcm = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE and len(pcm):
        n = int(round(len(pcm) * SAMPLE_RATE / rate))
        pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
    return pcm


def pcm_to_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _loud_frames(pcm: np.ndarray) -> np.ndarray:
    """One flag per 20 ms frame: clearly louder than the clip's own floor."""
    n = len(pcm) // _FRAME
    if n < 3:
        return np.zeros(0, bool)
    frames = pcm[: n * _FRAME].reshape(n, _FRAME)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    low = float(np.percentile(db, 10))
    floor = min(low, _FLOOR_CAP_DBFS)
    return (db > floor + _OVER_FLOOR_DB) & (db > low + _OVER_OWN_DB) & (db > _MIN_LEVEL_DBFS)


def has_speech(pcm: np.ndarray) -> bool:
    """True when the clip has a voice in it: frames clearly louder than the
    clip's own floor, for long enough. Silence and steady noise have no
    such frames. Deliberately simple; a single "si'" passes."""
    return int(_loud_frames(pcm).sum()) * 20 >= _MIN_SPEECH_MS


def trim_silence(pcm: np.ndarray) -> np.ndarray:
    """The clip from just before the first word to just after the last one.
    Whisper given seconds of silence invents words, often the answer again."""
    loud = np.flatnonzero(_loud_frames(pcm))
    if not len(loud):
        return pcm
    start = max(0, loud[0] * _FRAME - _MARGIN)
    end = min(len(pcm), (loud[-1] + 1) * _FRAME + _MARGIN)
    return pcm[start:end]


def speakable(text: str) -> str:
    """A reply as a voice should say it: "Casa / Manutenzione" is read
    "Casa, Manutenzione", not "Casa barra Manutenzione"."""
    return text.replace(" / ", ", ").replace("/", ", ")


# ── Interfaces ────────────────────────────────────────────────────────────────


class SpeechToText(Protocol):
    def transcribe(self, pcm: np.ndarray, prompt: str) -> str: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...


class WhisperCpp:
    """whisper.cpp through pywhispercpp, Italian forced."""

    def __init__(self, model_path: Path, language: str = "it") -> None:
        from pywhispercpp.model import Model
        self._model = Model(str(model_path), language=language, print_realtime=False,
                            print_progress=False, redirect_whispercpp_logs_to=os.devnull,
                            no_context=True, single_segment=True)
        self._lock = threading.Lock()

    def transcribe(self, pcm: np.ndarray, prompt: str) -> str:
        with self._lock:      # one whisper context, Streamlit runs sessions in threads
            segments = self._model.transcribe(pcm, initial_prompt=prompt)
        return " ".join(s.text for s in segments).strip()


class PiperSherpa:
    """A Piper voice (VITS onnx + espeak-ng data) run by sherpa-onnx."""

    def __init__(self, voice_dir: Path) -> None:
        import sherpa_onnx as so
        onnx = next(voice_dir.glob("*.onnx"))
        self._tts = so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(model=str(onnx), tokens=str(voice_dir / "tokens.txt"),
                                              data_dir=str(voice_dir / "espeak-ng-data")),
            num_threads=2)))
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        with self._lock:
            audio = self._tts.generate(text, sid=0, speed=1.0)
        return np.asarray(audio.samples, dtype=np.float32), audio.sample_rate


# ── The service ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Heard:
    text: str           # "" when nothing was said
    speech: bool        # False: the gate found no voice, the model never ran


_STT_FILE = "ggml-small-q5_1.bin"
_TTS_DIR = "vits-piper-it_IT-paola-medium"

_cache: dict[str, object] = {}
_cache_lock = threading.Lock()


class VoiceService:
    def __init__(self, stt: SpeechToText | None = None, tts: TextToSpeech | None = None,
                 root: Path | None = None) -> None:
        self._stt, self._tts = stt, tts
        self._root = root or models_dir()

    # Availability: the page shows the microphone only when the ears are there.

    def can_listen(self) -> bool:
        return self._stt is not None or ((self._root / _STT_FILE).exists() and _importable("pywhispercpp"))

    def can_speak(self) -> bool:
        return self._tts is not None or ((self._root / _TTS_DIR).is_dir() and _importable("sherpa_onnx"))

    def warm_up(self) -> None:
        """Load the models in the background, so the first answer does not
        wait for them."""
        def load():
            try:
                if self.can_listen():
                    self._ears()
                if self.can_speak():
                    self._voice()
            except Exception as exc:
                logger.warning(f"voice: warm up failed: {exc}")
        threading.Thread(target=load, daemon=True).start()

    def _ears(self) -> SpeechToText:
        if self._stt is None:
            self._stt = _shared("stt", lambda: WhisperCpp(self._root / _STT_FILE))
        return self._stt

    def _voice(self) -> TextToSpeech:
        if self._tts is None:
            self._tts = _shared("tts", lambda: PiperSherpa(self._root / _TTS_DIR))
        return self._tts

    # Ears

    def hear(self, wav: bytes, names: list[str] | tuple[str, ...] = ()) -> Heard:
        """The words in one recorded answer. *names* are the counterparts or
        categories on screen: put in the prompt, they are heard as written
        ("Esselunga", not "e se lunga")."""
        pcm = wav_to_pcm(wav)
        if not has_speech(pcm):
            logger.info("voice: no speech in the clip, the model did not run")
            return Heard("", False)
        prompt = _BASE_PROMPT + (" " + ", ".join(dict.fromkeys(n for n in names if n)) + "." if names else "")
        pcm = trim_silence(pcm)
        t0 = time.perf_counter()
        text = self._ears().transcribe(pcm, prompt)
        logger.info(f"voice: heard {len(pcm) / SAMPLE_RATE:.1f}s of audio in "
                    f"{(time.perf_counter() - t0) * 1000:.0f} ms")
        return Heard(text, True)

    # Voice

    def say(self, text: str) -> bytes:
        """The reply as WAV bytes, to play in the page."""
        samples, rate = self._voice().synthesize(speakable(text))
        # Bluetooth headphones switching back from the microphone eat the
        # first moment of sound: let them eat silence, not words.
        lead = np.zeros(int(rate * _LEAD_IN_S), np.float32)
        return pcm_to_wav(np.concatenate([lead, samples.astype(np.float32)]), rate)


def _importable(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _shared(key: str, build):
    """One model per process, built on first use."""
    with _cache_lock:
        if key not in _cache:
            _cache[key] = build()
        return _cache[key]
