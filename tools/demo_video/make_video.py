#!/usr/bin/env python3
"""Cut the raw recording into the 45-second first-use video, with subtitles
(documents/03_marketing/10_video_primo_uso.md).

Each scene of the recording is squeezed into its slot of the storyboard, so
the waits of the import never reach the screen; the subtitles are burnt in,
the audio is none. Out: first_use_<lang>.mp4 (H.264) and .webm.

Usage: python make_video.py REC_DIR [LANG]   (needs ffmpeg and Pillow)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# (scene start, scene end, seconds on screen, subtitle key). A longer scene is
# sped up to fit; a shorter one holds its last frame.
SLOTS = [("intro", "import", 5, "intro"), ("import", "import_done", 14, "import"),
         ("receipt", "receipt_done", 12, "receipt"), ("home", "end", 12, "home")]
CLOSING_SECONDS = 2

SUBTITLES = {
    "it": {"intro": "Le tue spese in ordine, sul tuo computer.",
           "import": "1. Trascina l'estratto conto della banca.",
           "receipt": "2. Hai uno scontrino? Dillo e spuntalo.",
           "home": "3. Guarda dove vanno i tuoi soldi.",
           "closing": "I tuoi dati restano sul tuo computer."},
    "en": {"intro": "Your spending in order, on your computer.",
           "import": "1. Drag in your bank statement.",
           "receipt": "2. Got a receipt? Say it and tick it.",
           "home": "3. See where your money goes.",
           "closing": "Your data stays on your computer."},
}


FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
FRAME = (1920, 1080)


def _subtitle_png(rec: Path, key: str, text: str, closing: bool = False) -> str:
    """The subtitle drawn as a transparent picture of the frame's size, laid
    over the video: ffmpeg builds without drawtext are common, a picture
    needs only the overlay filter.

    Args:
        rec: the recording folder, where the picture is written.
        key: the subtitle's slot, used as the file name.
        text: the line to show.
        closing: True for the closing line, bigger and in the middle of the
            frame; False for a subtitle at the bottom.
    """
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(FONT, 60 if closing else 50)
    picture = Image.new("RGBA", FRAME, (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width, height = right - left, bottom - top
    pad = 36 if closing else 26
    x = (FRAME[0] - width) // 2
    y = (FRAME[1] - height) // 2 if closing else FRAME[1] - 130 - height // 2
    draw.rectangle((x - pad, y - pad, x + width + pad, y + height + pad),
                   fill=(0, 0, 0, 178 if closing else 153))
    draw.text((x - left, y - top), text, font=font, fill="white")
    path = rec / f"sub_{key}.png"
    picture.save(path)
    return str(path)


def main() -> None:
    rec = Path(sys.argv[1])
    lang = sys.argv[2] if len(sys.argv) > 2 else "it"
    subs = SUBTITLES[lang]
    scenes = json.loads((rec / "scenes.json").read_text(encoding="utf-8"))
    pictures = [_subtitle_png(rec, key, subs[key]) for *_, key in SLOTS]
    pictures.append(_subtitle_png(rec, "closing", subs["closing"], closing=True))
    parts, labels = [], []
    for i, (start, end, seconds, key) in enumerate(SLOTS):
        a, b = scenes[start], scenes[end]
        speed = max((b - a) / seconds, 1.0)
        parts.append(f"[0:v]trim={a}:{b},setpts=(PTS-STARTPTS)/{speed:.4f},"
                     f"tpad=stop_mode=clone:stop_duration={seconds},trim=0:{seconds},"
                     f"setpts=PTS-STARTPTS[s{i}];[s{i}][{i + 1}:v]overlay=0:0[v{i}]")
        labels.append(f"[v{i}]")
    end = scenes["end"]
    closing_input = len(SLOTS) + 1
    parts.append(f"[0:v]trim={end - 0.1}:{end},setpts=PTS-STARTPTS,tpad=stop_mode=clone:"
                 f"stop_duration={CLOSING_SECONDS}[sc];[sc][{closing_input}:v]overlay=0:0[vc]")
    labels.append("[vc]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=1:a=0,fps=30[out]"
    inputs = ["-i", str(rec / "raw.webm")]
    for picture in pictures:
        inputs += ["-i", picture]
    for ext, codec in (("mp4", ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26",
                                 "-movflags", "+faststart"]),
                       ("webm", ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36"])):
        out = rec / f"first_use_{lang}.{ext}"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                        "-filter_complex", graph, "-map", "[out]", "-an", *codec, str(out)], check=True)
        print(out)


if __name__ == "__main__":
    main()
